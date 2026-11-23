# SQLAlchemy Migration Phase 3 — Workflows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Move every read and write of `workflows` and `workflow_assignments` from Prisma to SQLAlchemy repository modules, with no change in behaviour except the deliberate fixes listed below.

**Architecture:** Same as phases 1–2: one repository module per table in `src/db/repos/`, frozen-dataclass records, callers own transactions, and the **short-session rule** (no session open across network I/O, LLM calls, graph runs or polling waits). Executions, approvals and events stay on Prisma until phase 4, so the executor, external invoke and execution start keep their Prisma handle for those tables and read workflows through a sessionmaker.

**Tech Stack:** SQLAlchemy 2 async + psycopg 3, PostgreSQL 15, FastAPI, pytest + pytest-asyncio + pytest-httpx, pyright strict, ruff.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md) (phase 3 row of §3.3). Previous plans and their Outcome sections: [phase 1](2026-11-20-sqlalchemy-phase-1-settings-and-admin.md), [phase 2](2026-11-21-sqlalchemy-phase-2-integrations.md).

## Global Constraints

All Global Constraints from phases 1 and 2 apply unchanged (repos are the only query code; records not ORM objects; callers commit; `utcnow()`; explicit `updated_at` in ON CONFLICT `set_`; `add_or_already_exists`, never `session.add` first; repos imported as modules; short-session rule with an ordering test per function that mixes DB and slow work; routes fail closed with 503 "database unavailable" via `require_sessionmaker` when no database is attached; secrets never logged, class names only; CI-parity pyright `--pythonplatform Linux --pythonversion 3.12`; no edits to `prisma/`, `pyproject.toml`, `uv.lock`, `.github/**`; commits authored by Balaji Rajan `<balajirajan@gmail.com>` ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). Additionally:

- **Per-node secrets are the highest-risk behaviour.** `_to_workflow_read` redacts Jira `apiToken`, vector-DB `vectorDbApiKey`/`vectorDbEmbeddingApiKey`, Confluence `apiToken` and sensitive HTTP headers on every read path; create, update and the builder's save encrypt them and **preserve the stored value when the client echoes the redacted marker**, matched by node id against the previous row. Keep every branch, including the quirk that a redacted echo for a node id not in the previous row stores `None`.
- **Coordination:** Codex owns `src/telemetry/**` on `feat/telemetry-foundation` (not yet merged). Its branch also edits `src/api/internal.py` (Cloud Tasks claim handler) and `src/executors/agent.py`; whichever PR lands second rebases. Don't touch telemetry code.

## Decisions made while planning (2026-11-22)

| Topic | Decision |
|---|---|
| Search wildcards | The behaviour tests pin today's behaviour for `%`/`_` in `GET /workflows/search` (phase 1 found Prisma's `contains` does **not** escape them for user search; verify for workflows rather than assume). The port matches whatever is pinned. |
| Revoking a missing assignment | **No fix needed (corrected after Task 1):** Prisma Python's `delete()` returns `None` for a missing row, so today already answers 404 `assignment not found for user '<id>'` (pinned by `test_revoke_missing_assignment_today_404`). The port keeps it. |
| Search wildcards (result) | Task 1 confirmed `%` and `_` are LIKE wildcards today (as in phase 1). The port keeps wildcard semantics. |
| Redacted echo for a new node id (result) | Jira, vector-DB and Confluence store `None`; HTTP headers store the literal redacted marker (`prior.get(k, v)` falls back to the incoming value). Both pinned; the port keeps both. |
| `tags` NULL | Repo coalesces `NULL` to `[]` (Prisma always returned a list; `WorkflowRead.tags` is `list[str]`). |
| Owner assignment and grant | **Improvement:** today each splits a request across SQLAlchemy (user lookup) and Prisma (workflow write) with no atomicity. On SQLAlchemy, each becomes one transaction. No visible behaviour change. |
| Kept as-is | `create` ignores `isProduction`/`externalSlug`; concurrent edits are last-write-wins (no optimistic lock); builder save overwrites only `nodes`/`edges`; `poll_file_triggers` reads all production workflows unbounded; `executions.py` doesn't let assignees start runs (differs from the workflow routes). Follow-ups noted at the end. |
| `UniqueViolationError` 409s | `external_slug` conflicts (update, admin flags) and duplicate assignments map from `AlreadyExists`. Today's details embed Prisma's exception text after a fixed prefix; pin the **prefix only**, and the new tail names the constraint. |

---

## File structure

```
src/db/repos/
  workflows.py      NEW  WorkflowRecord; get, get_by_slug, list_page, search, count, create, update, delete,
                         list_production, delete_for_user, claim_orphans_by_email, insert_if_absent
  workflow_assignments.py NEW  AssignmentRecord; exists, list_for_workflow, create, delete, delete_involving_user
tests/integration/sqla_phase3/   NEW  behaviour tests (written first, run on Prisma)
tests/integration/db/repos/      NEW  test_workflows.py, test_workflow_assignments.py
```
Callers converted: `src/api/workflows.py` (all 21), `src/api/builder.py`, `src/api/executions.py`, `src/api/run.py`, `src/api/internal.py` (`poll_file_triggers` workflow read), `src/engine/langgraph_executor.py` (`_prepare_compiled`), `src/api/test_cleanup.py`, `src/migration/writer.py` + `runner.py`, `src/migration/reconcile.py`.

**Visibility and search must be exact.** Search: admin sees all; others see rows where `is_public OR user_id = :u OR EXISTS (assignment for :u)`, ANDed with `name ILIKE :q OR description ILIKE :q`. List: `mine` → `user_id = :u OR assigned`; admin without `mine` → all; others → public, own or assigned; optional `is_template`, `is_public`, `category` (exact) filters ANDed; `ORDER BY updated_at DESC` (no tiebreak today — keep, and have the tests assert order only on rows whose `updated_at` differ); `total` from a `count` with the same predicate. Express "assigned" as an `EXISTS` subquery, not a join (a join would duplicate rows).

---

### Task 1: Behaviour tests, proven on the Prisma code

In `tests/integration/sqla_phase3/`, copying phase 2's conftest approach (`phase3_env` applied before `create_app()`, `make_user`/`admin`/`member`/`second_member`, `bearer`, `ISO`, network lock with pytest-httpx, an ADAPTER AREA for anything that isn't HTTP). Lessons from the phase 1 and 2 reviews apply from the start: seed ≥3 rows in reverse order for ordering checks; backdate `updated_at` via SQL before updates and assert it advances; snapshot a bystander row in every update/delete test; pin exact statuses and details (prefix-only where Prisma text is embedded); search raw response text for secret values; check logs with caplog for secrets; never depend on pre-existing rows; clean up every row.

Groups:
1. **Create** — full payload incl. `isProduction`/`externalSlug` (pinned as ignored), tags, nodes/edges round trip; duplicate edges collapsed; size limits; invalid shape errors.
2. **Get visibility** — owner, assignee, public, admin → 200; others → 404 (exact detail).
3. **List** — the `mine`/admin/non-admin matrix, each optional filter, pagination (`limit`/`offset`, `total`), ordering by `updated_at DESC` on seeded rows with distinct timestamps.
4. **Search** — same visibility matrix, case-insensitive match on name and description only (not tags/category), `%`/`_` behaviour pinned, `offset=0` in the response, limit bounds.
5. **Update** — owner/assignee/admin allowed, others 403 (exact detail); bystander unchanged; `updated_at` advances; publish requires a valid slug (`_validate_slug` errors), unknown variable references rejected, unpublish clears the slug, slug conflict → 409 by prefix.
6. **Per-node secrets** — for each of Jira, vector-DB (both fields), Confluence and HTTP sensitive headers: create stores encrypted (decrypts to the value), every read path (get, list, search, create, update, admin flags, owner) returns the redacted marker and never the secret (raw-text search), an update echoing the marker preserves the stored ciphertext byte-for-byte, a changed value is re-encrypted, and a marker echoed for a node id not in the previous row stores `None` (pinned quirk).
7. **Delete** — owner/admin only (403 otherwise); cascade removes the workflow's assignments and executions at the DB level (seed both; assert gone); bystander workflow untouched.
8. **Admin flags** — admin only; empty payload returns the row with no write (`updated_at` unchanged); slug conflict 409 prefix.
9. **Owner assignment** — admin only; by id and by email (lowercased); unknown user 404 (exact); effect on visibility.
10. **Assignments** — list (owner/admin), grant (409 prefix on duplicate; unknown user 404), revoke; **revoking a missing assignment** pinned as today's behaviour (500 expected — confirm), to be updated in Task 3.
11. **Builder save** — through the adapter (scripted chat model, as phase 2 did for the agent executor): a builder turn persists only `nodes`/`edges` (metadata unchanged), secrets encrypted with preserve-on-echo, `updated_at` advances; visibility 404 for non-owner non-assignee.
12. **External invoke and execution start** — `POST /api/run/{slug}` requires `isProduction` and (public, owner or admin); unknown slug 404; `POST /workflows/{id}/execute` (or the real execution-start route) visibility (assignee NOT allowed, pinned).
13. **Executor workflow read** — through the adapter: running an execution loads its workflow; a deleted workflow gives today's error.
14. **Cleanup** — the e2e cleanup endpoint removes the user's workflows and every assignment where they are assignee or assigner.

- [x] Write the tests; run twice on unchanged code; all pass; tables empty afterwards. Where today differs from the expectation above, fix the test to match today and list each case in the commit.
- [x] Commit: `test(db): pin phase 3 workflow behaviour on the Prisma code`.
- [x] **Review gate:** Opus oracle review, fix, re-review, before Task 2.

---

### Task 2: Workflows repo and the workflow routes

**Files:** Create `src/db/repos/workflows.py`, `tests/integration/db/repos/test_workflows.py`. Modify `src/api/workflows.py` (create, get, list, search, update, delete, admin flags; owner assignment and assignments are Task 3).

**Interfaces (produce):**
```python
@dataclass(frozen=True)
class WorkflowRecord:
    id: str; user_id: str | None; name: str; description: str | None; category: str | None
    tags: list[str]                      # NULL coalesced to []
    difficulty: str | None; estimated_time: str | None
    nodes: list[dict[str, Any]]; edges: list[dict[str, Any]]
    version: str | None; is_template: bool; is_public: bool; is_production: bool
    external_slug: str | None; original_owner_email: str | None
    created_at: datetime; updated_at: datetime

class Visibility(TypedDict): user_id: str; is_admin: bool
async def get(session, workflow_id: str) -> WorkflowRecord | None
async def get_by_slug(session, slug: str) -> WorkflowRecord | None
async def list_page(session, *, viewer: Visibility, mine: bool, is_template: bool | None,
                    is_public: bool | None, category: str | None, limit: int, offset: int) -> tuple[list[WorkflowRecord], int]
async def search(session, *, viewer: Visibility, q: str, limit: int) -> tuple[list[WorkflowRecord], int]
async def create(session, values: WorkflowCreate) -> WorkflowRecord           # TypedDict
async def update(session, workflow_id: str, changes: WorkflowChanges) -> WorkflowRecord | None   # TypedDict(total=False); bumps updated_at; AlreadyExists on slug
async def delete(session, workflow_id: str) -> bool                           # DB cascades assignments + executions
async def list_production(session) -> list[WorkflowRecord]
async def delete_for_user(session, user_id: str) -> int
async def claim_orphans_by_email(session, email: str, user_id: str) -> int
async def insert_if_absent(session, values: WorkflowInsert) -> bool          # migration writer
```

- [x] Repo tests first (real Postgres): visibility predicates for list and search (incl. assignment via EXISTS, no duplicate rows when a workflow has several assignments), filters, pagination + count, ordering, ILIKE matching per the pinned wildcard behaviour, tags NULL → [], JSON round trip, update only changes given keys + deterministic `updated_at` + held-reference refresh, slug `AlreadyExists` with the constraint name and the session still usable, delete cascade (assignments and executions gone), `claim_orphans_by_email` idempotent.
- [x] Port the routes; keep the secret helpers' logic unchanged (they now read `existing.nodes` from a record); map `AlreadyExists` on slug to 409 with today's prefix.
- [x] Convert unit tests (`test_workflows_crud.py`, `test_workflows_{jira_tokens,vector_db_keys,confluence_tokens,http_headers}.py`, `test_workflows.py`, `tests/unit/security/test_hardening.py` workflow parts) to patched repo functions + `override_session`.
- [x] Verify: repo tests; unit tests; `tests/integration/sqla_phase3` (adapter-only edits) and the phase 1 and 2 behaviour suites; full unit suite; CI-parity pyright. Commit `feat(db): workflows on SQLAlchemy (phase 3)`.

---

### Task 3: Assignments, owner, cleanup and migration CLI

**Files:** Create `src/db/repos/workflow_assignments.py`, `tests/integration/db/repos/test_workflow_assignments.py`. Modify `src/api/workflows.py` (`_has_assignment`, owner assignment, list/grant/revoke assignments), `src/api/test_cleanup.py`, `src/migration/writer.py` + `runner.py`, `src/migration/reconcile.py`.

**Interfaces (produce):**
```python
@dataclass(frozen=True)
class AssignmentRecord: id: str; workflow_id: str; user_id: str; assigned_by_id: str; assigned_at: datetime
async def exists(session, workflow_id: str, user_id: str) -> bool
async def list_for_workflow(session, workflow_id: str) -> list[AssignmentRecord]    # today's order (unordered → none)
async def create(session, *, workflow_id: str, user_id: str, assigned_by_id: str) -> AssignmentRecord   # AlreadyExists on duplicate
async def delete(session, workflow_id: str, user_id: str) -> bool
async def delete_involving_user(session, user_id: str) -> int                         # user_id = :u OR assigned_by_id = :u
```

- [x] Repo tests first (duplicate → `AlreadyExists` with the session usable; delete returns False for a missing row; `delete_involving_user` covers both columns).
- [x] Port: owner assignment and grant become **one transaction each** (user lookup + write + one commit); revoke missing keeps today's **404** (pinned); `test_cleanup` uses `delete_for_user` + `delete_involving_user` in the same session as the other repos (its Prisma use for workflows is gone; executions still cascade at the DB level).
- [x] Migration writer/runner: `insert_if_absent` per row with per-row commit (phase 2 pattern; exception class + row id only in logs); reconcile uses `claim_orphans_by_email` in its session (its execution update stays Prisma until phase 4).
- [x] Convert unit tests (`test_workflow_assignments.py`, `test_test_cleanup.py`, `test_runner_writer.py`, `test_reconcile.py`). Verify as in Task 2. Commit `feat(db): workflow assignments on SQLAlchemy (phase 3)`.

---

### Task 4: Workflow readers outside the workflow routes

**Files:** Modify `src/api/builder.py` (`_load_workflow`, `_persist`), `src/api/executions.py` (workflow read), `src/api/run.py` (slug lookup), `src/api/internal.py` (`poll_file_triggers`' workflow list), `src/engine/langgraph_executor.py` (`_prepare_compiled`).

- [x] **Builder:** load the workflow in a short session and close it **before** `run_builder_turn` (the LLM loop); `_persist` writes nodes/edges in a fresh short session afterwards (same encrypt-with-preserve logic, against the row read at load time, as today). Ordering test.
- [x] **External invoke (`run.py`):** slug lookup in a short session closed before `enqueue_execution` and the sync polling wait. Ordering test.
- [x] **Execution start (`executions.py`):** workflow read in a short session; the execution create stays Prisma (phase 4).
- [x] **`poll_file_triggers`:** the production-workflow list read in a short session closed before Drive calls; its up-front 503 comment updated (only executions remain on Prisma).
- [x] **Executor:** `_prepare_compiled` reads the workflow through `get_registered_sessionmaker()` in a short session closed before `build_graph` and the run; keeps today's error when the workflow is missing; `self.db` remains for executions until phase 4 (mark with `# TODO(phase 4)`).
- [x] Convert unit tests (`test_builder_routes.py`, `test_builder_models_routes.py`, `test_executions*.py`, `test_run_external.py`, `test_internal_poll_file_triggers.py`, `tests/unit/engine/test_langgraph_executor.py`, `tests/unit/api/test_approval_email.py` if it reads workflows). Verify as in Task 2, plus `tests/integration/test_durable_execution.py` and `test_external_invoke.py`. Commit `feat(db): workflow readers on SQLAlchemy (phase 3)`.

---

### Task 5: Lock, docs, CI, PR

- [x] Grow `MIGRATED_PATHS` with every file now free of `prisma`/`src.storage` imports (list exclusions with reasons). Done-criterion grep: `grep -rnE "\b(db|prisma|self\.db)\.(workflow|workflowassignment)\." src` → empty.
- [x] CHANGELOG (`[Unreleased]`, first entry): the two tables on SQLAlchemy; behaviour proven by `tests/integration/sqla_phase3`; deliberate changes (revoke-missing 404; atomic owner assignment and grant; short sessions around the builder's LLM loop, external invoke's wait and the executor's run; 503 without a database); kept-as-is items and follow-ups.
- [x] `docs/architecture.md` data-access section; spec §3.3 phase 2 "(done, PR #29)" and phase 3 "(done, PR pending)"; this plan's checkboxes and an Outcome section.
- [x] Run everything CI runs, with a throwaway Postgres 15 (`composer-p3-pg`); delete it when the phase is done.
- [ ] Commit; push; open the PR; wait for all six checks; merge only with Balaji's go-ahead.

## Follow-ups (not in this phase)
- Concurrent edits are last-write-wins; consider an `updated_at` precondition (optimistic locking) for update and builder save.
- `poll_file_triggers` loads every production workflow; filter to workflows that have a file-trigger node.
- Execution start doesn't allow assignees, unlike the workflow routes; decide which is intended.
- A redacted secret echoed for a new node id stores `None`; reject it with a 422 instead.

## Outcome

What changed while executing this plan:

- **Review-gate fixes beyond the plan:** the owner check in grant and revoke runs under a row lock (`get(for_update=True)` takes FOR NO KEY UPDATE and refreshes an instance already in the session, so the check sees the locked row without blocking inserts of executions or assignments). A foreign-key race on grant gives 404 (`ReferencedRowMissing`; names the user when a user constraint is violated). The migration writer is strict about its input.
- **`test_migration.py` commits after reconcile**, because reconcile now leaves its writes to the caller.
- **The owner-assignment tests were converted** to the one-transaction behaviour (user lookup, write and a single commit) instead of kept as they were.
- **The builder re-checks access at save** (owner, admin or still assigned, under a row lock); a save after access was lost or the workflow vanished gives 404, commits nothing, and uses the same detail as the load-time check. Non-list `nodes` and `edges` in a migrated row still open as an empty canvas.
- **The executor raises a clear `RuntimeError`** (the execution is marked failed) when no sessionmaker is registered, instead of reading through Prisma.
- **`MIGRATED_PATHS`** gained `src/api/workflows.py` and `src/api/test_cleanup.py`. `builder.py`, `run.py`, `executions.py`, `internal.py`, `langgraph_executor.py` and `src/migration/*` stay off the list: they still import Prisma for executions, approvals or the Convex-sourced rows.
- **Found while reviewing:** `workflow_assignments` has a database foreign key only on `workflow_id`; `user_id` and `assigned_by_id` have none, so a grant to a user deleted mid-request succeeds rather than failing. The grant route still maps the user constraint names, should a later migration add them.
