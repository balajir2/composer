# Phase 7b — Workflow CRUD: Design

**Status.** Approved 2026-04-21.
**Related.** Phase 0 workflow storage (ADR-0002), Phase 7a auth middleware.

No new ADR: Phase 7b is conventional REST CRUD. Ownership + 404/403 error model follows Phase 7a's auth pattern.

---

## 1. Goal

Complete workflow API parity with OAB — ship the missing CRUD endpoints so an OAB-compat UI (Phase 10) can list, fetch, update, delete, and search workflows. Also add executions-list for execution discovery.

**Context.** Composer currently has only `POST /workflows` (create). OAB exposes full CRUD + list + search + metadata endpoints. Phase 7b fills the gaps the UI will need.

## 2. Non-goals

- **No import/export endpoints** (`import-markdown`, `export-langgraph`, etc.). UI-driven; Phase 10 can add if needed.
- **No workflow metadata sub-routes** (`/workflows/{id}/inputs`, `/metadata`, `/variables`, `/presets`). OAB keeps these separate for caching reasons; Composer returns all of it in `GET /workflows/{id}`. Users compute the derived views on the client.
- **No debug endpoints** (`/workflows/{id}/debug`, `/debug-execute`). Dev-tooling concerns; Phase 9+ if needed.
- **No file-storage endpoints** (`/upload`, `/api/files/*`). OAB has file nodes; Composer doesn't ship those.
- **No soft-delete.** Hard-delete with `ON DELETE CASCADE` (existing Prisma schema handles execution cascade).
- **No new ADR.** REST-CRUD conventions are well-established.

## 3. Endpoints

| Method | Path                          | Purpose                                   |
|--------|-------------------------------|-------------------------------------------|
| GET    | `/workflows`                  | List workflows (paginated, filterable)    |
| GET    | `/workflows/search`           | Search by name/description                |
| GET    | `/workflows/{workflow_id}`    | Fetch one                                 |
| PUT    | `/workflows/{workflow_id}`    | Update (owner-only; 403 otherwise)        |
| DELETE | `/workflows/{workflow_id}`    | Delete (owner-only; cascades executions)  |
| GET    | `/executions`                 | List executions (filter by `workflowId`, `userId`, `status`) |

(Plus the existing `POST /workflows`, `GET /executions/{id}`, `POST /executions`, `POST /executions/{id}/resume`, `GET /executions/{id}/events`.)

## 4. `GET /workflows` — list

**Query params:**
- `limit: int` — default 50, max 100.
- `offset: int` — default 0.
- `is_template: bool | None` — filter `isTemplate=...`. Alias `isTemplate`.
- `is_public: bool | None` — filter `isPublic=...`. Alias `isPublic`.
- `category: str | None` — exact match.
- `mine: bool | None` — if `true`, restrict to `userId=current_user_id`.

**Response:**
```json
{
  "total": 42,
  "items": [WorkflowRead, ...],
  "limit": 50,
  "offset": 0
}
```

**Auth.** `Depends(get_current_user_id)` — any authenticated user. No 403 (users can browse all public + template workflows). `mine=true` restricts to own.

**Ordering.** `ORDER BY updatedAt DESC`.

## 5. `GET /workflows/search` — name/description search

**Query params:**
- `q: str` — required; min length 1.
- `limit: int` — default 50, max 100.

**Implementation.** `WHERE name ILIKE '%{q}%' OR description ILIKE '%{q}%'`. Prisma has no native ILIKE; use raw query or `contains` with `mode: 'insensitive'`. **Decision: use Prisma `contains` + `mode: 'insensitive'`** (straightforward).

**Response:** same envelope as `GET /workflows` but no `offset` (search is top-N).

**No auth on search.** Any authenticated user can search.

## 6. `GET /workflows/{workflow_id}` — fetch one

**Path param:** `workflow_id: str`.
**Response:** `WorkflowRead`.
**Errors:** `404` if not found.
**Auth.** `Depends(get_current_user_id)` — any authenticated user. No ownership check (read-only; public + private both readable). If the workflow is owned by a different user, still readable — parity with OAB's behavior (workflows are world-readable unless hidden via future RBAC).

## 7. `PUT /workflows/{workflow_id}` — update

**Body:** same as `POST /workflows` (`WorkflowCreate`) — full replacement (not PATCH).

**Ownership enforcement:**
- If `workflow.userId != current_user_id` AND `dev_mode_fallback` didn't kick in → `403 Forbidden`.
- If workflow not found → `404`.

**Validation.** Same `validate_workflow_shape` as create — invalid workflows reject with `422`.

**Response:** updated `WorkflowRead`.

## 8. `DELETE /workflows/{workflow_id}` — delete

**Ownership enforcement:** same as update — `403` if not owner.
**Cascade.** Prisma schema has `onDelete: Cascade` on `WorkflowExecution.workflowId`. Deletion removes executions + their checkpoints automatically.
**Response:** `204 No Content` on success.
**Errors:** `404` if not found; `403` if not owner.

## 9. `GET /executions` — list executions

**Query params:**
- `workflow_id: str | None` — alias `workflowId`. Filter to one workflow's executions.
- `user_id: str | None` — alias `userId`. Filter by execution's userId.
- `status: str | None` — e.g., `"completed"`, `"failed"`, `"waiting_approval"`, `"running"`.
- `limit: int` — default 50, max 100.
- `offset: int` — default 0.

**Response:** `{total, items: list[ExecutionRead], limit, offset}`.

**Auth.** `Depends(get_current_user_id)` — any authenticated user. Phase 7+ may add per-user filtering; Phase 7b returns all matching rows.

**Ordering.** `ORDER BY startedAt DESC`.

## 10. Error model

| Condition                                     | HTTP | Detail                             |
|-----------------------------------------------|------|-------------------------------------|
| Unknown `workflow_id` (GET/PUT/DELETE)        | 404  | `"Workflow {id!r} not found."`     |
| Non-owner update/delete                       | 403  | `"Forbidden: not workflow owner."` |
| Validation error on update (shape)            | 422  | Validator's message                |
| Pydantic 422 on bad request body              | 422  | Default FastAPI                    |
| `limit > 100` / negative                      | 422  | Default FastAPI / custom validator |
| Empty search query `q=""`                     | 422  | `min_length=1`                     |

## 11. Pydantic models

Reuse existing `WorkflowCreate` + `WorkflowRead`. Add a paginated list envelope:

```python
class WorkflowListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    items: list[WorkflowRead]
    limit: int
    offset: int


class ExecutionListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    items: list[ExecutionRead]
    limit: int
    offset: int
```

## 12. Test plan

### 12.1 Unit tests

**`tests/unit/api/test_workflows_crud.py`** — ~15 tests:
- `GET /workflows` returns paginated envelope.
- `GET /workflows` with filters (`isTemplate`, `isPublic`, `category`, `mine`).
- `GET /workflows` limit validation (rejects `limit=150`, accepts `limit=50`).
- `GET /workflows/{id}` happy path → 200 + `WorkflowRead`.
- `GET /workflows/{id}` unknown → 404.
- `GET /workflows/search?q=foo` returns matching.
- `GET /workflows/search` empty `q` → 422.
- `PUT /workflows/{id}` as owner → 200 + updated.
- `PUT /workflows/{id}` as non-owner → 403.
- `PUT /workflows/{id}` unknown → 404.
- `PUT /workflows/{id}` invalid shape → 422.
- `DELETE /workflows/{id}` as owner → 204.
- `DELETE /workflows/{id}` as non-owner → 403.
- `DELETE /workflows/{id}` unknown → 404.
- Ordering by `updatedAt DESC` confirmed via mock.

**`tests/unit/api/test_executions_list.py`** — ~5 tests:
- `GET /executions` happy path envelope.
- `GET /executions?workflowId=X` filters.
- `GET /executions?status=completed` filters.
- Limit + offset pagination.
- Empty result set → `total=0, items=[]`.

### 12.2 Integration test

One real-Neon integration test: full CRUD cycle.
- `POST /workflows` create → get `id`.
- `GET /workflows/{id}` → 200.
- `GET /workflows` → new workflow is in list.
- `PUT /workflows/{id}` with updated name → 200.
- `GET /workflows/{id}` → updated name present.
- `DELETE /workflows/{id}` → 204.
- `GET /workflows/{id}` → 404.

## 13. Phase-exit checklist

- [ ] All unit tests green.
- [ ] Integration test green against real Neon.
- [ ] Ruff + format + pyright strict clean.
- [ ] `CHANGELOG.md` — Phase 7b section.
- [ ] `CLAUDE.md` — phase table: 7b → ✅, next is 7c (or 7d / Phase 8).

## 14. Self-review

- **Placeholders:** None.
- **Internal consistency:** response envelopes (`WorkflowListResponse`, `ExecutionListResponse`) consistent with §4, §5, §9. Ownership check pattern (`workflow.userId != current_user_id` → 403) consistent between PUT + DELETE.
- **Scope:** 6 endpoints + 2 list envelopes + ~20 tests. Single-plan territory.
- **Ambiguity:** Delete cascades (Prisma schema already has it). Hard-delete, not soft. Search uses Prisma `contains` with `mode: 'insensitive'`. Auth: any authenticated user can read; only owners can mutate.
