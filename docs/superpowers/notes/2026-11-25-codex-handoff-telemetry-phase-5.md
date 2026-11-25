# Handoff: telemetry roadmap Phase 5 on the merged execution repositories

Date: 2026-11-25. Applies once the combined SQLAlchemy phase 4 PR (4a + 4b) is merged. Read `docs/superpowers/plans/2026-11-20-telemetry-roadmap.md` Phase 5 first; this note names the exact paths to build on. Do not revive Prisma paths from older commits.

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
- Alembic: `src/db/migrations/versions/`, next revision after `0003_assignment_user_fks`. Copy the `_bound_locks()` helper style from 0002/0003 (bounded `lock_timeout`/`statement_timeout`), and create before drop. Schema changes never go through `prisma/` (frozen). Generate models from the Alembic schema, not `schema.prisma`.
- Tests: real-Postgres repo tests live in `tests/integration/db/repos/`; behaviour oracles in `tests/integration/sqla_phase4a` and `sqla_phase4b` must stay green.

## Still on Prisma (our SQLAlchemy phase 5, not yours)

The LangGraph checkpointer (`PrismaCheckpointSaver`) and its plumbing: `get_db`/`prisma_lifespan`/`get_checkpointer` and `enqueue_execution(db=)`. Do not touch until our phase 5 lands: `src/storage/**`, the checkpointer wiring in `src/engine/langgraph_executor.py`, and the dev-mode fallback in `src/execution/cloud_tasks.py`. LISTEN/NOTIFY (`src/engine/events_notify.py`, `src/api/events_ws.py`) is also phase 5.

## Operational note

Never point a 2 s Cloud Run startup probe at `/ready`. Balaji accepted occasional Neon cold-start 503s from `/ready`; a short probe would turn them into restart loops.
