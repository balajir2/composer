# Phase 8 — Security + hardening: Design

**Status.** Approved 2026-04-21.
**Related.** Phase 7a auth middleware (ADR-0014 + ADR-0015), Phase 7b workflow CRUD, Phase 5a user-approval, Phase 5b SSE, ADR-0021 (this phase — authz + rate-limit + size-cap policy).

---

## 1. Goal

Close the security gaps that make Composer production-shippable. Specifically:
- **Authz hardening**: workflow/execution read access respects ownership (world-open today).
- **Size limits**: reject workflows with too many nodes/edges, and execution inputs that are too large.
- **Rate limiting**: per-user throttling on expensive endpoints.
- **Security regression tests**: pin invariants that keep the above working.

## 2. Non-goals

- **No redis / cross-worker rate limiter.** Phase 8 ships in-memory per-worker. Multi-worker deployment is Phase 9.
- **No full audit logging.** Actions are logged via Python `logging` at INFO/WARNING already; structured audit log is Phase 9+.
- **No password-strength policy changes.** Phase 7a's bcrypt stands.
- **No pen-test or fuzzing.** Phase 8's security tests are regression tests for invariants, not adversarial exploration.
- **No RBAC / shared workflows.** Private = owner-only; public = world-read. No groups, no roles beyond `admin`/`member` (which already exists but goes unused in 8).
- **No SSRF protection.** The `http` executor can POST to any URL; private-network protection is Phase 9+ if demanded.

## 3. Authz policy (ADR-0021)

### 3.1 Workflows

| Scenario | Behavior |
|---|---|
| `GET /workflows/{id}` — workflow `is_public=True` | 200, any authenticated user |
| `GET /workflows/{id}` — private, caller is owner | 200 |
| `GET /workflows/{id}` — private, caller is NOT owner | **404** (not 403 — don't reveal existence) |
| `GET /workflows/{id}` — unknown id | 404 |
| `GET /workflows` (list) | Returns `is_public=True` rows + caller's private rows. Drops other users' private rows. `mine=true` further restricts to caller's own. |
| `GET /workflows/search` | Same filtering as list. |
| `POST /workflows` | Any authenticated user can create. Owner is set to caller. |
| `PUT /workflows/{id}` | Owner-only (already Phase 7b). 403 for non-owner. |
| `DELETE /workflows/{id}` | Owner-only (already Phase 7b). 403 for non-owner. |

**Rationale for 404 over 403 on read:** revealing that "workflow X exists but you're not the owner" is a minor info leak. 404 is strictly tighter.

### 3.2 Executions

Executions inherit from their `userId` field (set at create time from the caller). Workflow ownership does NOT grant execution access — a public workflow's executions are owned by whoever started them.

| Scenario | Behavior |
|---|---|
| `POST /executions` | Any authenticated user. Execution owner = caller. |
| `GET /executions/{id}` | Owner-only. 404 for non-owner (tight info-leak policy). |
| `POST /executions/{id}/resume` | Owner-only. 404 for non-owner. |
| `GET /executions/{id}/events` | Owner-only. 404 for non-owner. |
| `GET /executions` list | Returns caller's own + nothing else (no public-execution concept). |

**Dev-mode fallback (ADR-0015):** in `ENVIRONMENT=development`, `user_id` defaults to `"dev"`. All tests using dev-mode auth belong to user `"dev"`; integration tests must be consistent.

### 3.3 MCP servers

Already effectively owner-only via Phase 3a/3b. Phase 8 audits the existing behavior but makes no changes.

## 4. Size limits

Three defaults, settings-tunable:

```python
class Settings:
    max_workflow_nodes: int = 100     # MAX_WORKFLOW_NODES
    max_workflow_edges: int = 200     # MAX_WORKFLOW_EDGES
    max_execution_input_bytes: int = 1_000_000  # MAX_EXECUTION_INPUT_BYTES (1 MB)
```

**Enforcement timing:**
- **Workflow size** — at `POST /workflows` and `PUT /workflows/{id}` create/update. Reject with **422** + detail `"workflow exceeds max_nodes=100"` or `"max_edges=200"`. No changes at execution time (already-stored large workflows keep working — we only cap new ones).
- **Execution input size** — at `POST /executions`. JSON-serialize the input; if `len(serialized) > max_execution_input_bytes`, reject with **413** `"execution input exceeds max_bytes=1000000"`.

**Test plan:**
- 100-node workflow create = ok; 101-node = 422.
- 199 edges ok; 201 = 422.
- 500-byte input ok; 1.5 MB input = 413.

## 5. Rate limiting

### 5.1 Algorithm

**Token-bucket per user, in-memory.** For authenticated endpoints, key = `user_id`. For unauthenticated (login/register), key = client IP (`request.client.host`).

Storage: `dict[str, TokenBucket]` on `app.state.rate_limiters`. TokenBucket has `capacity`, `refill_per_second`, `last_refill`, `tokens`. Thread-safe via `asyncio.Lock`.

### 5.2 Per-endpoint caps

Defaults (settings-tunable):
- `POST /executions` — 30 requests/minute/user. (Expensive: spawns LLM calls.)
- `POST /executions/{id}/resume` — 60 requests/minute/user.
- `POST /auth/login` — 10 requests/minute per IP. (Brute-force surface.)
- `POST /auth/register` — 5 requests/minute per IP.
- `POST /auth/refresh` — 30 requests/minute per IP.
- `POST /mcp-servers/{id}/test-connection` — 10 requests/minute/user. (External network.)

Settings:
```python
rate_limit_executions_per_minute: int = 30
rate_limit_login_per_minute: int = 10
rate_limit_register_per_minute: int = 5
rate_limit_refresh_per_minute: int = 30
rate_limit_resume_per_minute: int = 60
rate_limit_mcp_test_per_minute: int = 10
```

**Not rate-limited (Phase 8):** list/search GETs, workflow CRUD, `/events` SSE (a subscription, not a new call), `/auth/me`, `/health`.

### 5.3 429 response shape

Status: **429 Too Many Requests**.
Headers: `Retry-After: <seconds>` (integer).
Body: `{"detail": "Rate limit exceeded. Retry after N seconds."}`.

### 5.4 Implementation shape

`src/security/rate_limit.py` (new):

```python
class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float) -> None: ...
    async def try_consume(self, cost: int = 1) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""


class RateLimiter:
    """In-memory per-key bucket registry, keyed by user_id or IP."""

    async def check(self, key: str, bucket_config: BucketConfig) -> tuple[bool, float]: ...


def get_rate_limiter(request: Request) -> RateLimiter:
    """FastAPI dependency."""
```

Lifespan wires `app.state.rate_limiter = RateLimiter()`.

### 5.5 ABC for future swap

`RateLimiter` is a concrete class in 8; Phase 9 can extract an `ABC` + add `RedisRateLimiter`. Not worth the ceremony in Phase 8.

## 6. Security tests

New test file `tests/unit/security/test_hardening.py`:

1. Special chars in workflow names (Unicode, emoji, SQL meta, HTML tags) round-trip unchanged through POST → GET. No SQL injection, no XSS (stored as-is, served as JSON).
2. Path-traversal in `workflow_id` path param (`../../etc/passwd`, `%2e%2e`) → 404 / 422 (FastAPI's path validator handles it; we assert the response).
3. Oversized payload — 5 MB workflow body → 413 (FastAPI's default or our explicit check).
4. Workflow with 101 nodes → 422.
5. Execution input at 1.5 MB → 413.
6. Two concurrent executions on the same workflow don't corrupt state (both return distinct execution_ids; both eventually succeed in parallel).
7. Rate-limit: 31st `POST /executions` in a minute → 429 with `Retry-After` header.
8. Rate-limit: 11th `/auth/login` in a minute from same IP → 429.
9. Rate-limit isolation: user A hitting limit doesn't affect user B (different buckets).
10. Authz: `GET /workflows/{id}` of user B's private workflow by user A → 404.
11. Authz: `PUT /workflows/{id}` of user B's workflow by user A → 403 (already exists; regression).
12. Authz: `GET /executions/{id}` of user B's execution by user A → 404.
13. Authz: `POST /executions/{id}/resume` of user B's execution by user A → 404.

## 7. Integration tests

One real-Neon test `tests/integration/test_security_hardening.py`:

- Register two users (A + B).
- A creates a **private** workflow.
- A creates a **public** workflow.
- B reads A's public workflow → 200.
- B reads A's private workflow → 404.
- B lists workflows → sees A's public but not A's private.
- B tries to update A's public workflow → 403.
- B tries to delete A's public workflow → 403.
- A executes private workflow → succeeds.
- B tries to read A's execution → 404.

(Rate-limit integration test is hard — requires real clock-waits. Defer to unit tests with mock clocks.)

## 8. Error model

| Condition | HTTP | Detail |
|---|---|---|
| Workflow nodes > max_workflow_nodes | 422 | `"workflow exceeds max_nodes=100; got N"` |
| Workflow edges > max_workflow_edges | 422 | `"workflow exceeds max_edges=200; got N"` |
| Execution input > max_execution_input_bytes | 413 | `"execution input exceeds max_bytes=1000000; got N"` |
| Rate limit exceeded | 429 | `"Rate limit exceeded. Retry after N seconds."` |
| Private workflow read by non-owner | 404 | `"Workflow {id!r} not found."` |
| Execution read/resume/stream by non-owner | 404 | `"Execution {id!r} not found."` |

## 9. ADR-0021 (summary — full entry in `docs/design/decisions.md`)

**Title.** Composer's security policy for Phase 8.

**Decision.**
- Authz: private workflows + all executions are owner-only-read; private reads by non-owners return 404 (tight info leak).
- Size caps: workflows ≤100 nodes / ≤200 edges; execution inputs ≤1 MB. Settings-tunable; enforced at write time.
- Rate limits: in-memory token bucket per user or IP. 30/min on `/executions`, 10/min on `/auth/login`, etc. 429 + `Retry-After` header on breach.
- Security regression tests pin the above invariants.

**Alternatives rejected.**
- Redis-based rate limiter: deferred to Phase 9 (multi-worker deployment).
- 403 on private-read: 404 is tighter.
- No size caps: user could DOS with enormous workflows.
- Rate-limit-on-storage-basis: complexity / operational load; in-memory is sufficient for single-worker.

**Consequences.**
- Tightening read-authz is a breaking change vs Phase 7b; existing integration tests must match dev-mode `user_id="dev"` or explicitly register second users.
- In-memory rate-limit state is lost on restart. Acceptable for single-worker dev/prod; Phase 9 replaces with Redis.
- Size caps are lenient (100 nodes) but non-zero. Real OAB workflows trend small (~10-20 nodes); the cap is defense-in-depth.
- `POST /executions` rate cap (30/min) is generous for normal use, rejecting runaway loops or API abuse.

**Implemented by.** Phase 8 (commits TBD).

**Related.** ADR-0014 (deployment mode), ADR-0015 (dev-mode auth), ADR-0016 (user-approval), ADR-0017 (SSE), Phase 7a/7b specs.

## 10. Phase-exit checklist

- [ ] All unit tests green.
- [ ] Integration test green (two users, authz boundaries, real Neon).
- [ ] Ruff + format + pyright strict clean.
- [ ] `CHANGELOG.md` — Phase 8 section.
- [ ] `CLAUDE.md` — phase table: 8 → ✅, next is Phase 9 (cutover) or Phase 10 (UI).
- [ ] ADR-0021 `Implemented by` backfilled.

## 11. Self-review

- **Placeholders:** None.
- **Internal consistency:** Settings keys match between §4 (size caps) and §5.2 (rate limits). 404 vs 403 policy pinned consistently (§3.1, §3.2, §8).
- **Scope:** 8 tasks. Big but single-phase (no 8a/8b/8c needed).
- **Ambiguity:** Rate-limit reset on restart — ok for single-worker. Concurrent-execution test proves isolation, not atomicity. Info-leak policy pinned (404 for private-read-by-non-owner).

## 12. Risks + future

- **Single-worker rate-limit**: if we scale horizontally, per-worker buckets allow 2× the intended limit. Documented + acceptable until Phase 9.
- **Size caps might be too tight.** 100 nodes is generous for OAB workflows but a few edge cases (template libraries, auto-generated workflows) could hit it. Setting-tunable as escape hatch.
- **Breaking changes.** Phase 7b tests that assumed world-read-on-GET now need two users or dev-mode `user_id="dev"`. Audit + fix in Task 1.
- **No SSRF protection.** The `http` executor can target internal URLs. Phase 9+ adds allowlist / blocklist if demanded.
- **No request-body size limit at ASGI level.** Phase 8 relies on FastAPI's default (no real limit). We validate shape-after-parse; very large payloads (>~100 MB) might OOM the worker. Phase 9 could add uvicorn `--limit-max-requests` + body-size middleware.
