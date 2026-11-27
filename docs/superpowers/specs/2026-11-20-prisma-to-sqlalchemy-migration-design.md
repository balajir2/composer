# Prisma → SQLAlchemy migration — design

**Date:** 2026-11-20
**Status:** Draft for review
**Supersedes:** ADR-0001 (custom Prisma checkpointer) and the "ORM: Prisma Python" entry in CLAUDE.md's stack table, once approved

## 1. Why

Prisma Client Python, Composer's ORM, is **archived and no longer maintained** (README warning since 2025-03, upstream issue #1073; last release 0.15.0 on 2024-08). It declares support only up to Python 3.12. It works today, and a Python 3.14 CI trial passed, but nothing upstream will fix it when it breaks on a future Python, Postgres or dependency release. It is also why the backend image has to install Node.js, and why migrations need Prisma's Node-based CLI.

This design moves Composer's database layer to **SQLAlchemy 2.0 (async) + Alembic on the psycopg 3 driver**. The database tables stay exactly as they are, so no data moves.

### Decisions made during design (2026-11-20)

| Topic | Decision |
|---|---|
| Feature work during the migration | Continues in parallel. The migration lands in small PRs that never block it. |
| How database code is tested | Real Postgres for database code. Route and engine tests fake repository functions, not the ORM. |
| LangGraph checkpointer | Switch to LangGraph's official `langgraph-checkpoint-postgres`, replacing our custom `PrismaCheckpointSaver`. |
| Postgres driver | psycopg 3 for everything: SQLAlchemy, the checkpointer, and LISTEN/NOTIFY. `asyncpg` is removed. |
| Code structure | Repository modules: named data-access functions per area. |
| Ids for new rows | UUIDv7 text from a small built-in generator; stdlib `uuid.uuid7()` replaces it on Python 3.15. Existing cuid ids are unchanged. |

### Starting point (measured on main at `d283f83`)

- 17 tables, 2 Postgres enum types, 20 Prisma migrations
- 234 Prisma query call sites in 43 `src` files; 2 raw SQL statements in one transaction (work claiming)
- 15 guarded status updates: writes that succeed only if the execution is still in the expected state, e.g. `update_many(where={"id": …, "status": "running"})`
- Infrastructure built on the Prisma client: `PrismaCheckpointSaver` (341 lines), `PostgresEventStore`, `PostgresRateLimiter`
- 57 unit-test files fake Prisma with about 420 `AsyncMock`s shaped like Prisma calls
- Production checkpoint data: 178 finished runs, 841 snapshots, 3.3 MB; **0 runs paused or active**

## 2. Architecture

```
src/db/
  engine.py        one async SQLAlchemy engine (psycopg 3, shared psycopg pool)
  session.py       get_session() FastAPI dependency; one unit of work per request
  ids.py           new_id() -> UUIDv7 as text
  types.py         UTCDateTime column type (see 4.3)
  models/          SQLAlchemy 2.0 typed models mapped onto the EXISTING tables
  repos/           one module per area; the only code that runs queries
alembic/           migrations from now on; first revision is an empty baseline
```

### 2.1 Rules

- **Only `src/db/repos/` runs queries.** Routes and the engine call named functions such as `executions.mark_completed_if_running(session, execution_id, output)` and get plain typed results back (dataclasses or Pydantic models), never live ORM objects.
- **Callers own transactions.** A route or engine step opens the session or transaction and passes it to repository functions. Multi-step writes, such as claiming work, are visibly atomic at the call site.
- **Guarded status updates are explicit.** Each is a named repository function that returns `True` if this caller won and `False` otherwise.
- **No hidden queries.** Relationships use `lazy="raise"`. Code that needs related rows loads them inside the repository function.

### 2.2 What replaces each Prisma-based piece

| Today | After |
|---|---|
| `PrismaCheckpointSaver` + `langgraph_checkpoints` / `langgraph_checkpoint_writes` | `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` on the shared psycopg pool; the old tables are retired after cutover (see 3.4) |
| `PostgresEventStore`, `PostgresRateLimiter` | Repository modules `repos/events.py` and `repos/rate_limits.py` |
| asyncpg LISTEN/NOTIFY (`src/engine/events_notify.py`) | psycopg notifications on a dedicated connection |
| `prisma migrate deploy` (CI, deploy workflow) | `alembic upgrade head` (the saver's tables are an Alembic revision, not `setup()`; see the phase 5 plan D3) |
| Node.js in the backend image (Prisma CLI and engines) | Removed |
| `prisma.errors.UniqueViolationError` (8 catch sites) | Repository-level `AlreadyExists` naming the violated constraint |

### 2.3 Dependencies

Added in phase 0: `sqlalchemy[asyncio]` (2.1), `alembic`, `psycopg[binary]` 3. Added in phase 5: `psycopg-pool` and `langgraph-checkpoint-postgres` (which needs `langgraph-checkpoint` ≥ 4.3; it was already 4.3.2 when phase 5 started). Removed in phase 6: `prisma`, `asyncpg`, `asyncpg-stubs` (and `jinja2`, `tomlkit`, which only Prisma needed).

### 2.4 Documentation updates (in the foundation PR)

- CLAUDE.md stack table: ORM → SQLAlchemy 2.0 async + Alembic; driver → psycopg 3; a rule that schema changes go through Alembic only.
- New ADR superseding ADR-0001, recording the checkpointer and driver decisions above.
- `docs/operations/production-deployment.md` and `gcp-cloud-run-setup.md`: migration commands.

## 3. Migration order and coexistence

### 3.1 Coexistence rules

1. **One schema owner from the first PR.** `prisma/schema.prisma` is frozen, and a CI check fails any PR that changes it. Every schema change, including from parallel feature work, is an Alembic revision.
2. **Schema parity is tested.** A CI test runs `alembic upgrade head` on the throwaway Postgres, then checks that the SQLAlchemy models match the real tables (Alembic autogenerate produces an empty diff).
3. **An area moves whole.** All reads and writes of an area switch ORMs in the same PR, so no transaction ever spans both ORMs.
4. **Same tables, same rows.** Rows written by either ORM are readable by the other. Both clients are connected during the transition, with pool sizes chosen so their total stays within Neon's connection limit.
5. **Deploys ran both migration tools during the transition:** the existing (by then no-op) `prisma migrate deploy`, then `alembic upgrade head`. Since phase 6 only Alembic remains.

### 3.2 Alembic baseline

The first Alembic revision is empty and is *stamped* on existing databases (production, and CI after `prisma migrate deploy`), so Alembic starts from the schema Prisma's 20 migrations produced. A fresh database during the transition is built by `prisma migrate deploy` + `alembic upgrade head`. After phase 6, `0001_baseline` creates the base schema with the exact SQL of Prisma's 20 migrations, and the existing revisions follow it (ADR-0037); no database needed a stamp.

### 3.3 Phases (one PR each)

| # | Scope | Risk | Rough size |
|---|---|---|---|
| 0 | **Foundation:** dependencies; `src/db` engine, session, ids, types; models for all 17 tables; Alembic baseline; parity test; schema-freeze CI check; deploy and CI steps; CLAUDE.md, ADR and ops docs. No behaviour change. | Low | 2–3 days |
| 1 | **Settings and admin (done, PR #27):** deployment settings, LLM models and keys, API keys, users | Low | 2 days |
| 2 | **Integrations (done, PR #29):** MCP servers, MCP OAuth tokens and state, cloud-storage connections | Medium | 2 days |
| 3 | **Workflows (done, PR #33):** CRUD, assignments, builder saves | Medium | 2 days |
| 4 | **Executions core (done: 4a and 4b in one PR, pending; phase 5 next):** create, list, delete, resume, approvals, events, rate limiter, claim and sweep, all 15 guarded status updates | High | 3–4 days |
| 5 | **Checkpointer and notifications (done, PR pending):** official saver, paused-run deploy guard, two-way copy tool, LISTEN/NOTIFY on psycopg | High | 2 days |
| 6 | **Removal (done, PR pending; see ADR-0037):** Prisma client and schema; Node.js and asyncpg out of the image; remaining Prisma-shaped mocks; Alembic builds the base schema (`0001_baseline`) | Low | 1 day |
| 7 | **Cleanup (about two weeks after phase 5 is stable):** Alembic revision that drops `langgraph_checkpoints`, `langgraph_checkpoint_writes` and `_prisma_migrations` | Low | ½ day |

Total: about 2½–3 weeks.

**Known schema divergence (phases 4 and 6).** `prisma/schema.prisma` intentionally still declares `@@unique([workflowId, idempotencyKey])` on `WorkflowExecution`, while the database (Alembic `0002_idempotency_per_user`) has the unique index `workflow_executions_workflow_id_user_id_idempotency_key_key` on `(workflow_id, user_id, idempotency_key)`. Prisma's schema is frozen, so it was not edited. Consequences: the phase 4 execution models and the phase 6 squashed base migration must be generated from Alembic (the live schema), never from `schema.prisma`; and `prisma db push` or `prisma migrate dev` must never be run against an Alembic-managed database, since they would try to restore the old index.

**Second divergence (Alembic `0003_assignment_user_fks`).** `workflow_assignments` also differs from `prisma/schema.prisma`, which declares `assignedById String` (non-null) and has no relation from `userId` or `assignedById` to `User`. The database has foreign keys `workflow_assignments_user_id_fkey` (ON DELETE CASCADE) and `workflow_assignments_assigned_by_id_fkey` (ON DELETE SET NULL), a nullable `assigned_by_id` and an index on it. The same rules apply: generate models and the squashed base from the live schema, never from `schema.prisma`. No Prisma code reads this table any more (phase 3), so the frozen model's non-null `assignedById` is never exercised.

### 3.4 Checkpointer cutover (phase 5)

- **What users see:** nothing. Run pages, history and the API read the executions table, not checkpoints. Only the executor reads checkpoints, to detect a pause and resume the same run (`src/engine/langgraph_executor.py`).
- **Paused-run deploy guard:** the deploy's migration step refuses the cutover if any execution is `waiting_approval` or `running` and still has its snapshots only in the old tables, unless those runs were copied first.
- **Two-way copy tool:** a CLI command copies one run's snapshots between the old saver and the official saver, in either direction, through LangGraph's standard `BaseCheckpointSaver` interface. It covers both a forced cutover with paused runs and a rollback.
- **Deletes:** the two places that delete checkpoint rows directly (`src/api/executions.py`) switch to the saver's `adelete_thread`. Until phase 7 they also clear the old tables.
- **Neon pooler:** psycopg connections use `prepare_threshold=None` (no server-side prepared statements), as transaction-mode pooling requires.
- **Rollback:** finished runs are unaffected. Runs that paused after the cutover exist only in the new tables, so a rollback runs the copy tool new → old for them first. The old tables are kept untouched until phase 7, so a rollback never needs a database restore.

### 3.5 Parallel feature work

- New code in an **already-migrated** area must use its repository module. A CI check rejects `prisma` imports in migrated areas, from an allow-list that shrinks each phase.
- New code in a **not-yet-migrated** area may still use Prisma; that area's phase PR ports it.
- Each phase PR converts its own tests. Prisma-shaped mocks disappear area by area, and the last ones go in phase 6.

## 4. Correctness and error handling

### 4.1 Guarded status updates

Each becomes `UPDATE … WHERE id = :id AND status = :expected [AND …] RETURNING id`, wrapped in a named function that returns `True` if this caller won. Callers keep their current decision logic, which today keys on Prisma's update count. Each function gets real-Postgres race tests: both orderings, plus genuinely concurrent attempts where exactly one wins.

### 4.2 Work claiming (`src/api/internal.py`, claim-and-run)

- `SELECT … FOR UPDATE SKIP LOCKED` with the same eligibility conditions: claimable status for the delivery kind, `lease_owner IS NULL`, `lease_expires_at IS NULL`.
- Today's 10-second transaction limit is reproduced with `SET LOCAL lock_timeout` and `statement_timeout` plus an overall `asyncio.timeout`, so a stuck claim fails fast instead of holding the row for the whole lease.
- The lease write (status, owner, expiry, `running_since`, `delivery_attempts + 1`) commits **before** any graph or external call. A test asserts this ordering.

### 4.3 Times: `UTCDateTime`

Prisma created `timestamp(3) without time zone` columns and writes UTC wall-clock values. `UTCDateTime` accepts only timezone-aware datetimes, stores them as naive UTC (compatible with existing rows), and returns aware UTC. A naive datetime raises an error instead of being silently misread. This removes the class of bug documented in the claim code, where a datetime bound as text failed only against real Postgres.

### 4.4 Column mapping

| Prisma | Postgres | SQLAlchemy |
|---|---|---|
| `String @id @default(cuid())` | `text` | `Text` primary key, default `new_id()` (UUIDv7) |
| `Json` (10) | `jsonb` | `JSONB`; values are replaced, never mutated in place |
| `Bytes` (3) | `bytea` | `LargeBinary` |
| `UserRole`, `ApprovalDecision` | Postgres enum types | `Enum(..., name=<existing>, create_type=False)` |
| `onDelete: Cascade` (4) | FK `ON DELETE CASCADE` | Left to the database; relationships use `passive_deletes=True` |
| `@updatedAt` (8) | plain timestamp column | `onupdate` sets UTC now on every update |
| `@@map` / `@map` names | snake_case table and column names | Table and column names mapped explicitly. ORM attributes are snake_case (`thread_id`, not Prisma's `threadId`). The API's camelCase field names keep coming from the Pydantic response models, so API output is unchanged. |

The parity test (3.1) checks all of the above against the real database.

### 4.5 Errors and connections

- Unique-constraint violations become `AlreadyExists(constraint=…)` at the repository boundary; callers that caught `UniqueViolationError` catch it instead.
- The engine keeps today's fail-fast Neon cold-start policy (`db_connect_max_attempts`, initial backoff, per-attempt timeout) and uses `pool_pre_ping`, because Neon may suspend between requests.
- The app lifespan opens the engine, the checkpointer pool and the notification connection, and closes each independently at shutdown, as today.

## 5. Testing

| Layer | Runs against | Proves |
|---|---|---|
| Repository tests (new, marker `integration`, which already means "needs real Postgres") | Real Postgres 15: CI service; locally a Neon test branch or Docker | Queries, guarded updates, constraints, timestamps |
| Race tests (new) | Real Postgres, concurrent sessions | Exactly one winner per guarded update and claim |
| API behaviour tests (new, per phase) | Real Postgres through FastAPI | The area behaves the same before and after |
| Route and engine unit tests (converted) | Fake repository functions | Business logic, without a database |
| Schema-parity test (new) | Postgres after `alembic upgrade head` | Models match the real tables |
| Existing integration and E2E suites | Real services | No user-visible regression |

**Proving behaviour is unchanged:** each phase PR first adds API-level behaviour tests for its area and runs them on the Prisma code. They must pass there, which shows the tests are right, and then pass unchanged on the SQLAlchemy code.

**Phase 5 extras:** copy-tool round-trip tests in both directions with resume; a test that the deploy guard refuses while a paused run exists; and, after deploy, a manual production check that a run pauses at an approval step and resumes.

### 5.1 Done criteria for each phase PR

1. No `prisma` import remains in the area (enforced by CI from then on).
2. Behaviour tests pass on Prisma before and on SQLAlchemy after; repository, race and parity tests pass.
3. CI is fully green: ruff check, ruff format check, pyright strict, the full test suite, frontend, E2E smoke, Trivy, audit.
4. After merge, the deploy is healthy and backend logs show no new errors for 24 hours before a high-risk phase (4 or 5) starts.

### 5.2 Overall done

No `prisma` import anywhere; Node.js and asyncpg removed from the backend image; Alembic is the only migration tool; CLAUDE.md, the ADR, the ops docs and the CHANGELOG are updated.

## 6. Out of scope

- Schema redesign or renames
- Query optimisation beyond like-for-like
- Moving the Anthropic API version (held at 2023-06-01; `langchain-anthropic` capped below 1.7)
- Python upgrade (planned for 3.15 after this migration)

## 7. Risks

| Risk | Mitigation |
|---|---|
| A guarded update or the claim behaves differently after porting | Named functions, real-Postgres race tests, behaviour tests proven on Prisma first |
| A paused run can't resume after the checkpointer cutover | Deploy guard, two-way copy tool, old tables kept until phase 7 |
| Timestamp drift between rows written by the two ORMs | `UTCDateTime` plus parity and repository tests on real columns |
| Parallel feature PRs conflict with or bypass the migration | Schema freeze, Alembic-only rule, CI import allow-list, small phase PRs |
| Connection exhaustion on Neon while both clients run | Pools for Prisma and psycopg are sized together to fit Neon's limit; Prisma's pool goes away in phase 6 |
| Neon pooler incompatibility with psycopg | `prepare_threshold=None` on pooled connections, tested in CI and on the first deploy |
