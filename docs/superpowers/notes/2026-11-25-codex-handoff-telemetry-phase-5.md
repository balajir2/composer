# Handoff: telemetry roadmap Phase 5 on the merged execution repositories

Date: 2026-11-25 (updated after SQLAlchemy phase 5). Applies once the combined SQLAlchemy phase 4 PR (4a + 4b) and the phase 5 PR are merged. Read `docs/superpowers/plans/2026-11-20-telemetry-roadmap.md` Phase 5 first; this note names the exact paths to build on. Do not revive Prisma paths from older commits.

## Build on these

- Models: `src/db/models/executions.py` (`WorkflowExecution`, `ExecutionEvent`, `Approval`). Records are frozen dataclasses in `src/db/repos/executions.py` (`ExecutionRecord`, `ClaimedExecution`, `PendingInstance`); repos return records, never ORM objects.
- Guarded transitions, all in `src/db/repos/executions.py`, each ONE `UPDATE ... WHERE <guard> RETURNING` that reports whether it won. Attach the attempt write to each:

| Lifecycle point | Function | Caller |
|---|---|---|
| Claim (initial and resume) opens an attempt | `claim(...)` -> `ClaimedExecution \| None` | `src/api/internal.py` claim-and-run |
| Pause closes the attempt | `mark_waiting_approval_if_running` | `LangGraphExecutor._mark_waiting_approval` |
| Resume trigger (actor/decision) | `resume_if_waiting` + `approvals.create` (`src/db/repos/approvals.py`) | `src/api/executions.py`, `src/api/approval_email.py` |
| Terminals | `mark_completed_if_running`, `mark_failed_if_running`, `cancel_if_active`, `mark_enqueue_failed_if_queued` | executor, claim crash handler, cancel and create routes |
| Sweeper terminals | `fail_orphaned_if_unchanged`, `fail_expired_approval_if_unchanged`, `fail_expired_lease_if_unchanged` | `src/maintenance/execution_sweeper.py` |

Add recovery transitions (telemetry Task 7.2) as another guarded function of the same shape next to `resume_if_waiting`.

## Conventions

- Callers own the transaction: repos never commit. Write the attempt row in the SAME transaction as the transition, and only when the guarded function returned a winner (that is what makes "at most one winning attempt" true).
- Short-session rule: no session is held across a graph run, LLM call, Cloud Tasks enqueue, email, or WebSocket wait. Resume commits first, then enqueues. Outside a request, get sessions from `src/db/runtime.py`; executors take a `sessionmaker`.
- Alembic: `src/db/migrations/versions/`, the Alembic head is now `0004_langgraph_saver_tables` (phase 5), so your `execution_attempts` revision must chain after it (`down_revision = "0004_langgraph_saver_tables"`); check `uv run alembic heads` shows one head before and after. Copy the `_bound_locks()` helper style from 0002/0003 (bounded `lock_timeout`/`statement_timeout`), and create before drop. Schema changes never go through `prisma/` (frozen). Generate models from the Alembic schema, not `schema.prisma`.
- Tests: real-Postgres repo tests live in `tests/integration/db/repos/`; behaviour oracles in `tests/integration/sqla_phase4a` and `sqla_phase4b` must stay green.

## Checkpointer after phase 5 (replaces the old "Still on Prisma" section)

The checkpointer is the official `langgraph-checkpoint-postgres` saver (`src/checkpoints/saver.py`, own psycopg pool, tables from Alembic 0004). `src/storage/**`, `get_db`, `prisma_lifespan` and `enqueue_execution(db=)` no longer exist; nothing in `src` imports Prisma or asyncpg (`tests/unit/db/test_prisma_import_boundary.py` enforces it). LISTEN/NOTIFY is on psycopg (`src/engine/notifications.py`, `src/engine/events_notify.py`).

Files you may now touch: `src/engine/langgraph_executor.py` (keep the resume-time `bring_forward` and the `checkpoint_missing` / `checkpoint_sync_failed` handling intact), `src/execution/cloud_tasks.py` (its dev-mode fallback uses the registered checkpointer), `src/api/events_ws.py` and the notification modules. Still off limits until phase 7: `src/checkpoints/legacy.py`, `copy.py`, `versions.py`, `sync.py`, the `composer checkpoints` CLI and the old `langgraph_checkpoint*` tables (rollback depends on them).

Two new `failure_code` values exist: `checkpoint_missing` (a resume found no saved state; the run fails instead of restarting) and `checkpoint_sync_failed` (copy-forward or read failed during a resume; old-table rows intact, recoverable). They are already in `src/telemetry/attributes.py` and `docs/telemetry/signal-catalog.md` (phase 5 added them there; no change needed from you, but account for them in dashboards, attempt results and recovery logic, and in `docs/api-reference.md`'s failure-code table, where they are listed).

Rollback and cutover runbook: `docs/operations/production-deployment.md` "Checkpointer cutover".

## Operational note

Never point a 2 s Cloud Run startup probe at `/ready`. Balaji accepted occasional Neon cold-start 503s from `/ready`; a short probe would turn them into restart loops.

## Phase 6 (Prisma removal), 2026-11-26

Phase 6 (branch `feat/sqlalchemy-phase-6`, plan `docs/superpowers/plans/2026-11-26-sqlalchemy-phase-6-removal.md`) removes Prisma, asyncpg and `prisma/`, and makes `0001_baseline` build the base schema on an empty database. Balaji confirmed D9: **your `0005_execution_attempts` merges first; phase 6 rebases onto it.** `0005`'s `down_revision = "0004_langgraph_saver_tables"` stays valid in either order (existing databases never re-run 0001; fresh ones run 0001 to 0005 in order).

**Phase 6 avoids** (until the rebase, then only comment-only cleanups): `src/db/migrations/versions/0005_execution_attempts.py`; `src/db/models/{__init__,execution_attempts,executions}.py`; `src/db/repos/{execution_attempts,executions}.py`; `src/api/{approval_email,executions,internal}.py`; `src/engine/langgraph_executor.py`; the sweepers; `tests/integration/db/test_alembic_0005_execution_attempts.py`; `tests/integration/db/repos/test_execution_attempt_*.py`; `tests/integration/execution_attempts/`; `tests/integration/sqla_phase4a/conftest.py`; `tests/integration/sqla_phase4b/{test_approval_email,test_resume_route}.py`; `tests/unit/api/{test_approval_email,test_executions_resume,test_internal}.py`; `tests/unit/db/test_models_metadata.py`; `tests/unit/engine/test_langgraph_executor.py`; `CHANGELOG.md` and your own docs.

**Codex must avoid during phase 6** (until the phase 6 PR merges):
- `src/db/migrations/versions/0001_baseline.py`
- `pyproject.toml`, `uv.lock`, `prisma/**`
- `Dockerfile`, `.dockerignore`, `.gitignore`, `.github/workflows/**`
- `tests/conftest.py`, `tests/integration/conftest.py`, `tests/regression/conftest.py`, `tests/_rate_limit_reset.py`, `tests/_checkpoint_reset.py`
- the 9 `NEEDS_LEGACY_PRISMA` modules: `tests/integration/test_user_approval_approved.py`, `test_user_approval_rejected.py`, `test_external_invoke.py`, `test_security_hardening.py`, `test_standalone_auth_lifecycle.py`, `test_workflow_assignments.py`, `test_durable_execution.py`, `test_migration.py`, `test_mcp_highspot.py`
- new files: `tests/integration/_rows.py`, `tests/integration/db/schema_catalog.py`, `tests/integration/db/test_baseline_schema.py`, `tests/fixtures/schema/**`, `tests/unit/db/test_baseline_frozen.py`, `tests/unit/test_build_files.py`, `tests/integration/test_seed_templates.py`, `tests/integration/test_promote_admin.py`
- `tests/unit/db/test_prisma_import_boundary.py`, `test_prisma_frozen.py`, `tests/unit/test_prisma_execution_schema.py`, `tests/unit/test_deploy_workflow.py`
- the unit test files with `app.state.db` mocks (plan Task 5 list)
- `scripts/**`, `frontend/scripts/**`, `CLAUDE.md`

**Must not add:** any `prisma` or `asyncpg` import, `app.state.db` assignment or `NEEDS_LEGACY_PRISMA` marker; do not rely on `_prisma_migrations` existing or on `prisma migrate deploy` in a test. After phase 6 Task 6 the boundary test fails on any of these.
