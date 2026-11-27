# SQLAlchemy Migration Phase 4a — Executions and Events — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every read and write of `workflow_executions` (except the approval-resume paths), `execution_events` and `rate_limit_buckets` from Prisma to SQLAlchemy repository modules, including the work claim, the stuck-execution sweeper and every guarded status update outside the resume paths, with no change in behaviour except the deliberate fixes listed below.

**Architecture:** Same as phases 1–3: one repository module per table in `src/db/repos/`, frozen-dataclass records, callers own transactions, the **short-session rule**. New in this phase: the claim is one atomic `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED) RETURNING`, each guarded transition is a named repo function built as one `UPDATE … WHERE <guard> RETURNING id`, event sequence numbers are allocated under a per-execution transaction-scoped advisory lock, and `LangGraphExecutor` takes an `async_sessionmaker` instead of a Prisma client. The approval-resume paths (`POST /executions/{id}/resume`, the emailed-link confirm and the `approvals` table) stay on Prisma until **phase 4b**; both ORMs read and write the same `workflow_executions` rows, never inside one transaction. The checkpointer and the LISTEN/NOTIFY transport stay as they are until **phase 5**.

**Tech Stack:** SQLAlchemy 2 async + psycopg 3, PostgreSQL 15, FastAPI, LangGraph, pytest + pytest-asyncio + pytest-httpx, pyright strict, ruff. LISTEN/NOTIFY stays on asyncpg in this phase.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md) (phase 4 row of §3.3, §4.1 guarded updates, §4.2 work claiming, "Known schema divergence"). Previous plans and their Outcome sections: [phase 1](2026-11-20-sqlalchemy-phase-1-settings-and-admin.md), [phase 2](2026-11-21-sqlalchemy-phase-2-integrations.md), [phase 3](2026-11-22-sqlalchemy-phase-3-workflows.md).

## Global Constraints

All Global Constraints from phases 1–3 apply unchanged:

- Repos are the only query code; repos are imported as modules (`from src.db.repos import executions as executions_repo`).
- Repos return frozen-dataclass records, never ORM objects; they never commit. Callers commit.
- Writes take timestamps from `utcnow()` (`src/db/types.py`), passed in by the caller as `now` where a test needs to control time.
- Inserts that can collide use `add_or_already_exists` / `unique_violation_as_already_exists` (`src/db/errors.py`), never `session.add` first. Callers that caught `prisma.errors.UniqueViolationError` catch `AlreadyExists` instead.
- **Short-session rule.** A SQLAlchemy session pins its pooled connection from its first query until commit/rollback/close; Prisma never did. NEVER hold a session across a graph run (`compiled.ainvoke`/`aget_state`), an LLM call, network I/O (Cloud Tasks enqueue, Resend email, Drive, MCP), `asyncio.sleep` polling, or a WebSocket wait. Open `async with sessionmaker() as s:` for each DB step and close it before the slow step. Every function that mixes DB work and slow work gets a unit ordering test using `RecordingSessionmaker` + `assert_no_network_inside_session` (`tests/unit/db_fakes.py`).
- Out-of-request code takes an `async_sessionmaker[AsyncSession]` parameter; routes pass `require_sessionmaker(request)` (503 `database unavailable` when none is attached, fail closed); executors and the dev-mode Cloud Tasks fallback use `get_registered_sessionmaker()` and raise a clear `RuntimeError` when it is `None`.
- Never log secrets or execution payloads (inputs, outputs, variables): exception class names and ids only, as today.
- CI-parity pyright before every commit: `uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests`. Also `uv run ruff format --check src tests`.
- No edits to `prisma/`, `pyproject.toml`, `uv.lock`, `.github/**`, `src/db/migrations/**` (this phase needs no schema change).
- Commits authored by Balaji Rajan `<balajirajan@gmail.com>` (verify with `git log -1 --format='%an <%ae>'`), ending with one `Co-Authored-By:` line naming the model that actually did the work, e.g. `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

Additionally, for this phase:

- **Guarded status updates are the highest-risk behaviour.** Each one listed in "Guarded updates" below becomes ONE named repo function that issues ONE `UPDATE workflow_executions SET … WHERE id = :id AND <exact guard> RETURNING id` and returns `True` iff a row came back. Never read-then-write, never `session.get()` + attribute assignment, never ORM unit-of-work flush for these. The SET list is exactly today's `data=` dict, column for column (no extra columns, none missing). Callers keep their decision logic.
- **The claim commits before any graph or external call.** Pinned by an oracle test (the fake executor reads the row through a separate session) and a unit ordering test.
- **Executions models come from the live schema (Alembic), not `schema.prisma`.** The unique index is `workflow_executions_workflow_id_user_id_idempotency_key_key` on `(workflow_id, user_id, idempotency_key)` (Alembic `0002`). Never `find_unique` on the old pair; never run `prisma db push`/`migrate dev`.
- **Two ORMs, same rows.** In 4a the resume route and the email confirm still write `status`, `running_since`, lease columns and `variables` through Prisma, and `claim`, the sweeper and the executor read them through SQLAlchemy. Each side commits each statement before the other side can act, so no transaction spans both ORMs. Keep `ExecutionRead` accepting both Prisma rows (camelCase attributes, via its aliases) and records (explicit `_to_execution_read`), until 4b.

## Decisions made while planning (2026-11-24)

**Confirmed by Balaji on 2026-11-24:** D2 and D3 are fixed in 4a. D1 and D5–D11 are accepted as recommended. **D4 was split out** into the security hotfix PR #37 (`fix(builder): only diagnose the caller's own runs of the workflow being built`), which is cherry-picked onto this branch. Oracle group 13 therefore pins the **fixed** behaviour: no `test_diagnosis_today_ignores_owner`. Task 6 ports `build_diagnosis` like-for-like, keeping the filter.

| # | Topic | Recommendation |
|---|---|---|
| D1 | **Executor in 4a or 4b.** The executor's only "approval" path is `_mark_waiting_approval`, a guarded write to `workflow_executions` (it never touches `approvals`). | **4a moves the whole executor**, `_mark_waiting_approval` included. After 4a `LangGraphExecutor` holds **no** Prisma handle (`self.db` is removed, not kept for 4b). The 4b paths (resume, email confirm) never call the executor; they only enqueue. |
| D2 | **Event sequence numbers.** Today `append` computes `count(*) + 1` and inserts, non-atomically. Two bugs follow: (a) parallel graph branches append concurrently, one insert hits the `(execution_id, seq)` unique index and `_emit` swallows the error, so the event is lost; (b) after `sweep_old_execution_events` deletes an execution's oldest events, `count + 1` equals an existing `seq`, so **every** later event of that execution is lost. | **Fix:** `pg_advisory_xact_lock(hashtextextended('execution_events:' \|\| :execution_id, 0))`, then `INSERT … SELECT COALESCE(MAX(seq), 0) + 1 … RETURNING seq`, committed at once. Gap-free and monotonic per execution, no retries. This is a documented exception to `src/db/locks.py`'s non-blocking rule: the lock is held for one INSERT and the commit. Alternative (not recommended): max+1 with a bounded retry on `AlreadyExists`, which fails under fan-out wider than the retry cap. Oracle pins today's serial behaviour; the retention-gap test is the one test Task 3 is allowed to change (listed). |
| D3 | **Rate limiter atomicity.** Today: read, compute refill in Python, write, so concurrent requests for one bucket can over-admit. | **Fix:** same algorithm, but the read is `SELECT … FOR UPDATE` in a short transaction, so concurrent checks of one bucket serialize. Serial results (allowed, retry-after, no write on deny) unchanged and pinned. Repo race test: N concurrent checks on a bucket with k tokens → exactly k allowed. |
| D4 | **Builder diagnosis reads any execution (IDOR).** `build_diagnosis(db, execution_id)` (`src/builder/diagnostics.py:34`) loads the execution by id with no owner or workflow check, so a member editing their own workflow can pull another user's execution status, error, node results and last 30 variables (redacted only by key name) into the LLM context. | **Fix in 4a**, as phase 2 fixed OAuth state consumption in its migration PR: diagnose only an execution whose `workflow_id` is the builder's workflow and whose `user_id` is the caller (admins: any execution of that workflow). Otherwise behave as for a missing execution (no diagnosis). The oracle pins today's leak in `test_diagnosis_today_ignores_owner`; Task 6 replaces that test (listed). If Balaji prefers a separate security PR, Task 6 ports like-for-like and the fix follows. |
| D5 | **Claim shape.** Spec §4.2 describes `SELECT … FOR UPDATE SKIP LOCKED` then the lease write. | One statement: `UPDATE … WHERE id = (SELECT id … FOR UPDATE SKIP LOCKED) AND <eligibility> RETURNING id, variables`, with `SET LOCAL lock_timeout = '5s'`, `SET LOCAL statement_timeout = '10s'` and an `asyncio.timeout(10)` around the transaction (today: Prisma `tx(timeout=10000)`). Same eligibility, same lease columns, same commit-before-run. The resume decision comes from `RETURNING variables` instead of a second read after the commit (same row, read under the lock; nothing writes `variables` between the two today). |
| D6 | **Execution delete atomicity.** Today single and bulk delete issue four autocommitted Prisma deletes (checkpoint writes, checkpoints, events, execution). | **Improvement:** one SQLAlchemy transaction: `DELETE … WHERE id IN (…) AND status NOT IN (active) RETURNING id, thread_id`, then the checkpoint writes, checkpoints and events for exactly those rows. The extra status guard closes nothing today (terminal statuses are absorbing) but costs nothing. Checkpoint rows are deleted through a new `src/db/repos/checkpoints.py` (`delete_threads`); phase 5 replaces it with the saver's `adelete_thread` + old-table clear. No visible change. |
| D7 | **LISTEN/NOTIFY transport.** The spec assigns "LISTEN/NOTIFY on psycopg" to phase 5. | **Stays asyncpg in 4a** (`src/engine/events_notify.py` NOTIFY, `src/api/events_ws.py` LISTEN). 4a ports only the event persistence and the WebSocket's execution read, and pins ordering: an event's row is committed before its NOTIFY is sent. Phase 5 design note in Follow-ups. |
| D8 | **Checkpointer.** | **Out of scope (phase 5).** `PrismaCheckpointSaver`, `app.state.checkpointer`, `get_checkpointer` and the checkpoint tables' reads/writes are untouched. `enqueue_execution(…, db=…)` keeps its `db` argument **only** to build the dev-mode fallback's `PrismaCheckpointSaver`; mark it `# TODO(phase 5)`. |
| D9 | **Kept as-is (pinned).** Cancel's response carries the pre-update row with only `status` and `error` replaced (so `completedAt` is `null` in the response although the row has it). Resume-kind claim with no `_resume_decision` answers 422 and leaves the fresh lease in place until the lease sweeper fails it (superseded 2026-11-27: `c849fe0` fails the row at once, see the [4b follow-ups](2026-11-24-sqlalchemy-phase-4b-approvals-and-resume.md)). `list_executions` orders by `started_at DESC` with no tiebreak. External invoke (`run.py`) does not mark a row failed when the enqueue raises (only `POST /executions` does). The `sweep_expired_approvals` guard compares the whole `variables` JSONB for equality. | Keep all; follow-ups listed. |
| D10 | **Pool size.** Per-node event appends, the per-node `ensure_execution_running` read and every rate-limit check move from Prisma's pool to SQLAlchemy's (`db_pool_size=2`, `db_max_overflow=3`). | Keep the defaults in 4a (both pools must fit Neon's limit while Prisma remains). Each of these holds a connection for one or two statements. Task 7 records pool checkout waits from the full suite and the staging deploy; revisit in 4b or phase 6. |
| D11 | **Context variable.** `src/engine/context.py`'s `set_current_db`/`get_current_db` carry the Prisma client only so `events_wrapper` can call `ensure_execution_running` per node. | Replace with `set_current_sessionmaker`/`get_current_sessionmaker` (same "skip the check when unset" semantics). `mcp.py`/`download_pdf.py` keep their verbatim `set_current_db` error wording (phase 2 decision). |

---

## 4a / 4b boundary: every execution-related Prisma call site (main at `2368b23`)

Found with `grep -rnE "\.(workflowexecution|approval|executionevent|ratelimitbucket|langgraphcheckpoint|langgraphcheckpointwrite)\.|query_raw|execute_raw|\.tx\(" src` plus the Prisma handles passed around (`self.db`, `db: Any`, `get_current_db`).

| File:line | Call | PR | Reason |
|---|---|---|---|
| `src/api/_idempotency.py:25` | `workflowexecution.find_first` (per-user key) | 4a | idempotency lookup |
| `src/api/events_ws.py:107` | `workflowexecution.find_unique` | 4a | WebSocket authz + snapshot read |
| `src/api/executions.py:234` | `update_many` queued→failed (enqueue failure) | 4a | create path, guarded G1 |
| `src/api/executions.py:284-285` | `count`, `find_many` | 4a | list |
| `src/api/executions.py:302` | `find_unique` | 4a | get |
| `src/api/executions.py:398,421,422` | `find_many`, `executionevent.delete_many`, `delete_many` | 4a | bulk delete |
| `src/api/executions.py:415,418` | `langgraphcheckpointwrite/langgraphcheckpoint.delete_many` | 4a (D6) | deleted in the same transaction via `repos/checkpoints.py` |
| `src/api/executions.py:457,484,485` (+482,483 checkpoints) | `find_unique`, events/execution delete | 4a | single delete |
| `src/api/executions.py:506,539` | `find_unique`, `update_many` active→canceled | 4a | cancel, guarded G2 |
| `src/api/executions.py:577,624,649,659` | read, `update_many` waiting_approval→running, `approval.create`, unguarded `variables` update | **4b** | resume: approvals + resume |
| `src/api/approval_email.py:78,177,197,207` | read, `update_many` waiting_approval→running, `approval.create`, `variables` update | **4b** | emailed-link resume |
| `src/api/internal.py:228-283` | `tx` + `query_raw` (FOR UPDATE SKIP LOCKED) + `execute_raw` lease write | 4a | claim G5 |
| `src/api/internal.py:361` | `find_unique` (`_resume_decision`) | 4a | inside claim (reads what 4b's Prisma code stamped) |
| `src/api/internal.py:387` | `update_many` running→failed (crash handler) | 4a | guarded G6 |
| `src/api/internal.py:470,498-526` | `sweep(db)` → sweeper | 4a | sweep route |
| `src/api/internal.py:543,568,717` | `db` for executor + `enqueue_execution(db=)` | 4a (executor) / phase 5 (`db` kept for the enqueue fallback's checkpointer only) | |
| `src/api/run.py:136-176,207` | idempotency lookup, `start_execution`, `UniqueViolationError`, sync-poll `find_unique` | 4a | external invoke |
| `src/builder/diagnostics.py:35` | `find_unique` | 4a (D4) | builder diagnosis |
| `src/builder/builder_agent.py:242,273-274`, `src/api/builder.py:196,253` | `db` passed to diagnosis | 4a | becomes the sessionmaker |
| `src/engine/events_pg.py:45,47,59` | `executionevent.count/create/find_many` | 4a (D2) | event store |
| `src/engine/execution_control.py:19` | `find_unique` | 4a | cooperative stop check |
| `src/engine/events_wrapper.py:177-201` | `get_current_db()` + `ensure_execution_running(db, …)` | 4a (D11) | per-node stop check |
| `src/engine/langgraph_executor.py:149,155` | `create`, `find_unique` | 4a | start + load |
| `src/engine/langgraph_executor.py:204,327,401` | `set_current_db`, `ensure_execution_running(self.db, …)` | 4a | |
| `src/engine/langgraph_executor.py:230,267,283` | `update_many` running→completed / waiting_approval / failed | 4a (D1) | guarded G7–G9 |
| `src/execution/cloud_tasks.py:117-123` | `PrismaCheckpointSaver(db)`, `PostgresEventStore(db)`, `claim_and_run_execution(…, db, …)` | 4a (event store, claim) / phase 5 (checkpointer) | dev-mode fallback |
| `src/maintenance/execution_sweeper.py:60,66,118,143,179,187,234` | `find_many`, `update_many` ×3, `executionevent.delete_many` | 4a | sweeper G10–G12 + retention |
| `src/maintenance/execution_sweeper.py:240-293` | `_sweeper_loop(db)`, `start_sweeper(app, db)` | 4a | local loop |
| `src/main.py:71-77` | `start_sweeper(app, db, …)` | 4a | lifespan body |
| `src/storage/db.py:88,91` | `PostgresEventStore(db)`, `PostgresRateLimiter(db)` | 4a | constructed in `src/main.py` instead |
| `src/security/rate_limit_pg.py:41,49,66,86` | `ratelimitbucket.find_unique/create/find_unique/update` | 4a (D3) | rate limiter |
| `src/migration/writer.py:78-85` | `upsert_execution` | 4a | migration CLI |
| `src/migration/runner.py:108-145` | `Prisma()` for executions | 4a | migration CLI (Prisma client removed from runner) |
| `src/migration/reconcile.py:36,49-62` | `update_many` claim orphans; `Prisma()` | 4a | reconcile CLI |
| `src/storage/checkpointer.py:*` | checkpoint tables | phase 5 | D8 |

**Counts:** 58 Prisma query call sites touch these tables outside `src/db` and the checkpointer (the grep above, restricted to query methods, `query_raw`, `execute_raw` and `tx`). 4a converts 50 of them (plus the Prisma handles threaded through 12 files); 4b keeps 8 (`executions.py:577,624,649,659`, `approval_email.py:78,177,197,207`). After 4a `grep -rnE "\b(db|self\.db)\.(workflowexecution|executionevent|ratelimitbucket)\." src` lists only those eight lines, and `grep -rn "\.approval\." src` only `executions.py:649` and `approval_email.py:197`.

**How 4a leaves the 4b paths working.** `resume_execution` and `confirm_approval_email` keep `db: Prisma = Depends(get_db)` and their code byte-for-byte, except: (1) `resume_execution` returns `ExecutionRead.model_validate(execution)` of a Prisma row, which `ExecutionRead` still accepts by alias; (2) both still call `enqueue_execution(id, kind="resume", db=db)`, whose `db` now only feeds the dev-mode checkpointer. The claim they trigger is SQLAlchemy: it sees their committed `status='running'`, `lease_owner IS NULL`, `lease_expires_at IS NULL` and `variables._resume_decision` because each Prisma statement autocommits before `enqueue_execution` runs. `tests/integration/test_user_approval_approved.py`, `test_user_approval_rejected.py`, `test_durable_execution.py::test_resume_clears_stale_lease_and_reclaim_succeeds` and the oracle's resume group prove the cross-ORM hand-off.

## Guarded updates

The spec's "15" counted every `update_many` call at `d283f83`: the 11 guarded execution transitions below (G1–G4, G6–G12), plus `admin_users.py` API-key revocation (moved in phase 1) and three `reconcile.py` orphan claims (workflows and MCP servers moved in phases 2–3; the execution one moves here as a plain repo function, not a race-relevant guard). Add the claim (G5, raw SQL, not `update_many`) and there are **12 race-relevant execution transitions: 10 in 4a, 2 in 4b.**

| # | Site (main `2368b23`) | Guard (exact) | SET (exact) | 4a repo function | Race the oracle pins |
|---|---|---|---|---|---|
| G1 | `api/executions.py:234` | `id AND status = 'queued'` | status `failed`, error `failed to enqueue execution for durable processing: {exc}`, completed_at | `mark_enqueue_failed_if_queued` | enqueue fails after a concurrent cancel: stays `canceled` |
| G2 | `api/executions.py:539` | `id AND status IN ('queued','running','waiting_approval')` | status `canceled`, error `Execution canceled by user request.`, completed_at, running_since/lease_owner/lease_expires_at NULL | `cancel_if_active` | two cancels: one 200, one 409; cancel vs executor completion: exactly one wins |
| G3 | `api/executions.py:624` | `id AND status = 'waiting_approval'` | status `running`, running_since, lease NULL | **4b** | |
| G4 | `api/approval_email.py:177` | `id AND status = 'waiting_approval'` | same as G3 | **4b** | |
| G5 | `api/internal.py:228-283` | row lock (SKIP LOCKED) on `id AND status = ANY(kind's statuses) AND lease_expires_at IS NULL AND lease_owner IS NULL`; run → `queued`, resume → `running` | status `running`, lease_owner = worker, lease_expires_at = now+lease, running_since = now, delivery_attempts + 1 | `claim` | 5 concurrent claims: exactly one runs, delivery_attempts = 1; leased/terminal/wrong-kind rows: `already_claimed` |
| G6 | `api/internal.py:387` | `id AND status = 'running'` | status `failed`, error `{Type}: {exc}`, failure_code `execution_error`, completed_at, running_since/lease NULL | `mark_failed_if_running` | crash after a concurrent cancel: `already_terminal`, row stays `canceled` |
| G7 | `engine/langgraph_executor.py:230` | `id AND status = 'running'` | status `completed`, output, variables, node_results, completed_at, lease/running_since NULL | `mark_completed_if_running` | completion vs cancel; completion vs lease sweep |
| G8 | `engine/langgraph_executor.py:267` | `id AND status = 'running'` | status `waiting_approval`, variables (merged pending markers), lease/running_since NULL (completed_at untouched) | `mark_waiting_approval_if_running` | pause vs cancel: if cancel wins, no approval email and no `approval_required` event |
| G9 | `engine/langgraph_executor.py:283` | `id AND status = 'running'` | as G6 with `failure_code` = `execution_error` or `execution_timeout` | `mark_failed_if_running` (same function as G6) | failure vs cancel |
| G10 | `maintenance/execution_sweeper.py:66` | `id AND status = 'running' AND lease_expires_at IS NULL AND running_since < :cutoff` | status `failed`, failure_code `orphaned_running`, error (exact template), completed_at, lease/running_since NULL | `fail_orphaned_if_unchanged` | claim/renewal between scan and write: not failed (scanned 1, marked 0) |
| G11 | `maintenance/execution_sweeper.py:143` | `id AND status = 'waiting_approval' AND variables = :snapshot::jsonb` | status `failed`, error (exact template), completed_at (no failure_code, lease untouched) | `fail_expired_approval_if_unchanged` | resume or a newer pause between scan and write: not failed |
| G12 | `maintenance/execution_sweeper.py:187` | `id AND status = 'running' AND lease_expires_at = :scanned_value` | status `failed`, failure_code `lease_expired`, error (exact), completed_at, lease/running_since NULL | `fail_expired_lease_if_unchanged` | lease renewed (new expiry) between scan and write: not failed; completion vs sweep: one wins |

Not guarded today and kept: the resume `variables` stamps (`executions.py:659`, `approval_email.py:207`, both 4b) and `reconcile.py:36` (`user_id IS NULL` filter; idempotent).

---

## File structure

```
src/db/repos/
  executions.py     NEW  ExecutionRecord, ClaimedExecution, ExecutionRef, ExecutionInsert;
                         create, get, get_status, find_by_idempotency_key, list_page,
                         list_refs, delete_terminal, claim, the 8 guarded functions,
                         list_orphaned_running, list_waiting_approval, list_expired_leases,
                         claim_orphans_by_email, insert_if_absent
  events.py         NEW  EventRecord; append (advisory-locked max+1), list_since,
                         delete_for_executions, delete_older_than
  rate_limits.py    NEW  BucketRecord; lock_bucket (FOR UPDATE), insert_bucket, save_bucket
  checkpoints.py    NEW  delete_threads (phase 5 replaces it with adelete_thread)
src/engine/events_pg.py        PostgresEventStore(sessionmaker) facade over repos/events.py
src/security/rate_limit_pg.py  PostgresRateLimiter(sessionmaker) facade over repos/rate_limits.py
tests/integration/sqla_phase4a/  NEW  behaviour oracle (written first, run on Prisma)
tests/integration/db/repos/      NEW  test_executions.py, test_executions_races.py,
                                      test_events.py, test_rate_limits.py, test_checkpoints.py
```

Callers converted: `src/api/executions.py` (all but resume), `src/api/_idempotency.py`, `src/api/run.py`, `src/api/internal.py` (claim, crash handler, sweep route, poll's executor), `src/api/events_ws.py` (execution read + event reads), `src/api/builder.py` + `src/builder/builder_agent.py` + `src/builder/diagnostics.py`, `src/engine/langgraph_executor.py`, `src/engine/execution_control.py`, `src/engine/events_wrapper.py`, `src/engine/context.py`, `src/engine/events_pg.py`, `src/execution/cloud_tasks.py`, `src/maintenance/execution_sweeper.py`, `src/security/rate_limit_pg.py`, `src/storage/db.py` (stops constructing the event store and limiter), `src/main.py` (lifespan body only), `src/migration/writer.py` + `runner.py` + `reconcile.py`.

Column names: records are snake_case (`workflow_id`, `lease_expires_at`); `ExecutionRead` keeps its camelCase aliases, so API JSON is unchanged. Oracle tests assert exact response key sets.

---

### Task 1: Behaviour oracle, proven on the Prisma code

**Files:** Create `tests/integration/sqla_phase4a/__init__.py`, `conftest.py`, and the test modules listed per group. Copy phase 3's conftest approach (`phase4a_env` applied before `create_app()`, `make_user`/`admin`/`member`/`second_member`, `bearer`, `ISO`, `unique`, `HTTPX_MARK` network lock with loopback excluded, `Db` SQLAlchemy seed/inspect helpers, a `Phase4aAdapter` ADAPTER AREA). `make_user` teardown also deletes, for the users' workflows' executions, their `execution_events` and checkpoint rows (no FK: they do not cascade).

**Seams that do not change across the port** (say which in the commit):
- `src.api.executions.enqueue_execution` / `src.api.run.enqueue_execution` / `src.api.internal.enqueue_execution`: monkeypatched per test (record calls, optionally run claim-and-run over ASGI exactly as `tests/integration/conftest.py`'s `_drive_cloud_tasks_synchronously`, or raise).
- `src.api.internal.LangGraphExecutor`: replaced by a fake class (`__init__(self, **kwargs)`, `async run(id)`, `async resume(id, decision)`) when a test is about the claim, not the graph. The fake reads the row through a **separate** SQLAlchemy session inside `run`, which is how the oracle proves the lease committed before the run.
- `POST /internal/claim-and-run` and `POST /internal/sweep` over HTTP (`ENVIRONMENT=development`, `CLOUD_TASKS_SERVICE_ACCOUNT` unset → OIDC bypass).
- Everything else that is not HTTP goes through `Phase4aAdapter` (only its bodies and the ADAPTER AREA imports may change later):

```python
class Phase4aAdapter:
    async def executor_run(self, execution_id: str) -> None: ...            # real LangGraphExecutor.run
    async def executor_resume(self, execution_id: str, decision: str) -> None: ...
    async def start_execution(self, workflow_id: str, user_id: str | None, run_input: Any,
                              idempotency_key: str | None = None) -> str: ...  # returns id; raises DuplicateKey
    async def mark_completed(self, execution_id: str, final_state: dict[str, Any]) -> bool: ...
    async def append_event(self, execution_id: str, type_: str, payload: dict[str, Any]) -> int: ...
    async def list_events_since(self, execution_id: str, after_seq: int) -> list[dict[str, Any]]: ...
    async def sweep_orphaned(self, *, grace_seconds: int, now: datetime,
                             after_scan: Callable[[], Awaitable[None]] | None = None) -> tuple[int, int, int]: ...
    async def sweep_approvals(self, *, timeout_hours: int, now: datetime,
                              after_scan: Callable[[], Awaitable[None]] | None = None) -> tuple[int, int, int]: ...
    async def sweep_leases(self, *, now: datetime,
                           after_scan: Callable[[], Awaitable[None]] | None = None) -> tuple[int, int, int]: ...
    async def sweep_events(self, *, retention_days: int, now: datetime) -> int: ...
    async def rate_check(self, route_key: str, client_key: str, capacity: int,
                         refill_per_second: float) -> tuple[bool, float]: ...
    async def diagnose(self, execution_id: str, *, workflow_id: str, user_id: str, role: str) -> dict[str, Any] | None: ...
```

`after_scan` runs between the sweep's candidate query and its guarded writes. On Prisma the adapter passes a `SimpleNamespace(workflowexecution=SimpleNamespace(find_many=<real then hook>, update_many=<real>))` (the seam `test_durable_execution.py::test_sweeper_stale_read_cannot_fail_attempt_claimed_after_selection` already uses); after the port it monkeypatches `executions_repo.list_*` to call the real function and then the hook. `DuplicateKey` is an ADAPTER AREA exception the adapter raises for `UniqueViolationError` today and `AlreadyExists` later.

Lessons from phases 1–3 apply from the start: seed ≥3 rows in reverse order for ordering checks; snapshot a bystander row (all columns) in every write test; pin exact statuses, details and error strings; check every response's exact key set; never depend on pre-existing rows; clean up every row (executions, events, checkpoints, approvals); concurrent tests use `asyncio.gather` over ≥5 attempts where "exactly one" is claimed.

Groups (module → what it pins):

1. **`test_create.py`** — 202 with exact keys `{id, workflowId, userId, status, currentNodeId, nodeResults, variables, input, output, error, failureCode, startedAt, completedAt, threadId}`, `status="queued"`, ISO `startedAt`, UUID `threadId`; DB row: `node_results={}`, `variables={}`, `delivery_attempts=0`, and whether `input=None` is stored as SQL `NULL` or JSON `null` (`SELECT input IS NULL, jsonb_typeof(input)`; pin what today does); 413 exact detail; invisible workflow 404 exact; assignee allowed. The row is committed before `enqueue_execution` is called (the fake enqueue reads it through a separate session). Enqueue raises → 500 `Execution '<id>' could not be enqueued for processing.`, row `failed`, error starts `failed to enqueue execution for durable processing: `, `completed_at` set, `failure_code` NULL. Fake enqueue that cancels the row (SQL) and then raises → row stays `canceled` (G1 guard).
2. **`test_idempotency.py`** — same user + key → same id, one row, enqueue called once; another user + same key → two rows; replay after completion returns the completed row; 5 concurrent same-key POSTs → one id, one row, one enqueue; same on `POST /api/run/{slug}` (replay skips enqueue, `is_replay`). (The 409 "key already used" path needs the old index and is unit-tested only.)
3. **`test_list_get.py`** — list: member sees own only, admin all, `workflowId`/`status` filters, `limit`/`offset`, `total`, `limit=101` → 422, `started_at DESC` on rows with distinct `started_at`; get: owner/admin 200, other user and missing → 404 `Execution '<id>' not found.`
4. **`test_delete.py`** — single: owner/admin 204; other 404; each active status 409 with exact detail; the row's events, checkpoint rows (seeded into both checkpoint tables for its `thread_id`) and approvals are gone; a bystander execution with its events and checkpoints is untouched. Bulk: `allInScope` for a member 403 exact; empty body 422 exact; member ids are intersected with own; active skipped; exact `deletedCount`/`skippedCount` for a mixed request (2 own terminal, 1 own active, 2 other users' → 2 / 3); admin `allInScope` deletes terminal rows only (asserts on seeded rows; this deletes every terminal execution in the test database, which is the throwaway Postgres).
5. **`test_cancel.py`** — each active status → 200, response `status="canceled"`, `error` exact, **`completedAt` null in the response** (pinned D9), DB row `completed_at` set and lease/running_since NULL; terminal → 409 `Execution is '<status>', not cancellable.`; non-owner 404; 5 concurrent cancels → one 200, four 409 with the "no longer cancellable" detail; cancel vs `adapter.mark_completed` in both orders and gathered → exactly one winner, final row consistent with the winner.
6. **`test_claim.py`** (fake executor) — run claims `queued` only; resume claims `running` with no lease only; leased, terminal and wrong-kind rows → `{"status": "already_claimed"}` and row unchanged; on a win the fake sees (separate session) `status='running'`, `lease_owner` = the worker id, `lease_expires_at` ≈ now + `execution_lease_seconds` (±5 s), `running_since` set, `delivery_attempts` + 1; 5 concurrent claims → one `completed`, four `already_claimed`, `delivery_attempts == 1`; resume passes `variables._resume_decision` to `resume`; resume without it → 422 `decision is required when kind='resume'` **and the new lease stays** (pinned D9); fake `run` raises → 500 generic detail, row `failed`, error `RuntimeError: <msg>`, `failure_code='execution_error'`, lease NULL; fake `run` cancels the row then raises → `{"status": "already_terminal"}`, row `canceled`.
7. **`test_executor.py`** (real graphs via the adapter; rows made `running` first) — start→end → `completed`, output, variables, node_results, `completed_at`, lease NULL, events `workflow_started … workflow_completed` with seq 1..n; failing transform → `failed`, `failure_code='execution_error'`, error `<Type>: …`; user-approval node → `waiting_approval`, `_pending_approval_node/_prompt/_since` in variables, `completed_at` NULL, `approval_required` event last; run on a row that is not `running` → row unchanged, no events; an `http` node whose `httpx_mock` callback cancels the row → row stays `canceled`, no `workflow_completed` event, exact event-type list pinned as observed.
8. **`test_events.py`** — serial appends get seq 1..n; `list_since(after)` returns seq > after ascending with the stored payload unflattened; **`test_append_after_retention_gap_today_collides`**: delete an execution's two oldest events, then append → today raises (`DuplicateKey`) — the one test Task 3 changes (to: append returns max+1). WebSocket (Starlette `TestClient` on its own app, every DB setup/inspection through `client.portal.call(...)` so it runs on the app's loop, following `tests/integration/test_events_ws_hardening.py`): snapshot type per status (`waiting_approval` → `approval_required`; terminal → `workflow_completed`, close 1000 `terminal`; else `workflow_started`); `?after=N` replays only seq > N and closes after a terminal event; an event appended + notified while connected is delivered; missing execution 4404, non-owner 4403, admin allowed, no bearer 4401.
9. **`test_sweeps.py`** — orphaned: past grace and lease NULL → `failed`, `orphaned_running`, exact error; younger or leased → untouched; `after_scan` renewal → (scanned 1, marked 0). Approvals: `timeout_hours=0` → (0, 0, 0); old `_pending_approval_since` → `failed` with exact error, lease untouched; naive or malformed `since` skipped; `after_scan` that resumes the row or re-pauses with a new `since` → not failed. Leases: expired → `failed`, `lease_expired`; `after_scan` renewal → not failed; completion vs sweep gathered → one winner. Events retention: rows older than the cutoff deleted, count returned, newer kept; `retention_days=0` → `ValueError`. `POST /internal/sweep` returns the four keys.
10. **`test_rate_limit.py`** — first check creates the bucket with capacity − 1; capacity checks allowed then denied with exact `retry_after`; a deny does not move `last_refill`; backdated `last_refill` refills; two concurrent first checks with capacity 2 → both allowed, tokens 0, one row. Route level: `RATE_LIMIT_EXECUTIONS_PER_MINUTE=2` → third `POST /executions` is 429 with `Retry-After`.
11. **`test_external_invoke.py`** — async mode: `status`, `streamUrl` exact shape; sync mode polls to `completed` with output; sync returns early at `waiting_approval`; sync with a no-op enqueue and `timeoutSeconds=1` returns the async shape with `status="queued"`.
12. **`test_resume_crossover.py`** (4b paths unchanged, proves the 4a claim reads what they wrote) — pause a real approval graph, `POST /executions/{id}/resume` → claim (SQLAlchemy after the port) runs the resume to `completed`; one `approvals` row; same via the emailed-link confirm.
13. **`test_diagnosis.py`** — via `adapter.diagnose`: status, error, secret-hint keys redacted, values > 500 chars truncated, last 30 variables; **`test_diagnosis_today_ignores_owner`**: another user's execution of another workflow is diagnosed (pinned D4) — replaced in Task 6 if D4 is approved.

Migration writer and reconcile are covered by `tests/integration/test_migration.py` (extend it if it does not already assert an execution row's insert-if-absent and the email claim) plus the repo tests, as in phase 3.

- [x] Write the tests; run twice on unchanged code against the throwaway Postgres (`DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head` first); all pass; tables empty of test rows afterwards. Where today differs from the expectation above, fix the test to match today and list each case in the commit.
- [x] Commit: `test(db): pin phase 4a execution and event behaviour on the Prisma code`.
- [x] **Review gate:** Opus oracle review (would a plausible wrong port pass?), fix, re-review, before Task 2.

---

### Task 2: Executions repository and race tests (no callers yet)

**Files:** Create `src/db/repos/executions.py`, `src/db/repos/checkpoints.py`, `tests/integration/db/repos/test_executions.py`, `tests/integration/db/repos/test_executions_races.py`, `tests/integration/db/repos/test_checkpoints.py`.

**Interfaces (produce):**

```python
ACTIVE_STATUSES: tuple[str, ...]   # sorted(src.api.execution_status.ACTIVE_EXECUTION_STATUSES); import, don't copy

@dataclass(frozen=True)
class ExecutionRecord:
    id: str; workflow_id: str; user_id: str | None; status: str; current_node_id: str | None
    node_results: Any; variables: Any; input: Any; output: Any        # raw JSON, as Prisma returned it
    error: str | None; failure_code: str | None
    started_at: datetime; completed_at: datetime | None; thread_id: str
    original_owner_email: str | None; idempotency_key: str | None
    lease_owner: str | None; lease_expires_at: datetime | None; running_since: datetime | None
    delivery_attempts: int

@dataclass(frozen=True)
class ClaimedExecution: id: str; variables: Any

@dataclass(frozen=True)
class ExecutionRef: id: str; thread_id: str; status: str

class ExecutionInsert(TypedDict):          # migration writer; strict keys
    id: str; workflow_id: str; status: str; thread_id: str; node_results: Any; variables: Any
    user_id: NotRequired[str | None]; original_owner_email: NotRequired[str | None]
    current_node_id: NotRequired[str | None]; input: NotRequired[Any]; output: NotRequired[Any]
    error: NotRequired[str | None]

async def create(session, *, workflow_id: str, user_id: str | None, input: Any, thread_id: str,
                 idempotency_key: str | None = None) -> ExecutionRecord   # status 'queued'; AlreadyExists
async def get(session, execution_id: str) -> ExecutionRecord | None
async def get_status(session, execution_id: str) -> str | None
async def find_by_idempotency_key(session, *, workflow_id: str, user_id: str, key: str) -> ExecutionRecord | None
async def list_page(session, *, owner_id: str | None, workflow_id: str | None, status: str | None,
                    limit: int, offset: int) -> tuple[list[ExecutionRecord], int]   # owner_id None = all
async def list_refs(session, *, ids: list[str] | None, owner_id: str | None) -> list[ExecutionRef]  # ids None = all
async def delete_terminal(session, ids: list[str]) -> list[ExecutionRef]   # also deletes their events + checkpoints
async def claim(session, execution_id: str, *, kind: Literal["run", "resume"], worker_id: str,
                now: datetime, lease_expires_at: datetime) -> ClaimedExecution | None
async def mark_enqueue_failed_if_queued(session, execution_id: str, *, error: str, now: datetime) -> bool      # G1
async def cancel_if_active(session, execution_id: str, *, error: str, now: datetime) -> bool                  # G2
async def mark_failed_if_running(session, execution_id: str, *, error: str, failure_code: str,
                                 now: datetime) -> bool                                                       # G6, G9
async def mark_completed_if_running(session, execution_id: str, *, output: Any, variables: Any,
                                    node_results: Any, now: datetime) -> bool                                 # G7
async def mark_waiting_approval_if_running(session, execution_id: str, *, variables: Any) -> bool             # G8
async def list_orphaned_running(session, *, cutoff: datetime) -> list[ExecutionRecord]
async def fail_orphaned_if_unchanged(session, execution_id: str, *, cutoff: datetime, error: str, now: datetime) -> bool   # G10
async def list_waiting_approval(session) -> list[ExecutionRecord]
async def fail_expired_approval_if_unchanged(session, execution_id: str, *, variables_snapshot: Any,
                                             error: str, now: datetime) -> bool                                # G11
async def list_expired_leases(session, *, now: datetime) -> list[ExecutionRecord]
async def fail_expired_lease_if_unchanged(session, execution_id: str, *, lease_expires_at: datetime,
                                          error: str, now: datetime) -> bool                                   # G12
async def claim_orphans_by_email(session, email: str, user_id: str) -> int
async def insert_if_absent(session, values: ExecutionInsert) -> bool    # ON CONFLICT (id) DO NOTHING

# src/db/repos/checkpoints.py
async def delete_threads(session, thread_ids: list[str]) -> None   # writes first, then checkpoints
```

Every guarded function has this shape (G7 shown; the others differ only in guard and SET):

```python
async def mark_completed_if_running(
    session: AsyncSession, execution_id: str, *, output: Any, variables: Any,
    node_results: Any, now: datetime,
) -> bool:
    won = await session.scalar(
        sa_update(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id, WorkflowExecution.status == "running")
        .values(
            status="completed", output=_json(output), variables=variables, node_results=node_results,
            completed_at=now, lease_owner=None, lease_expires_at=None, running_since=None,
        )
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    return won is not None
```

`_json(value)` returns `JSON.NULL` for `None` where Task 1 pinned that Prisma stores JSON `null` (`Json(None)`), and `None` (SQL NULL) where it pinned SQL NULL; decide per column from the oracle (`input` on create, `output` on completion).

The claim (D5):

```python
_CLAIMABLE = {"run": ("queued",), "resume": ("running",)}

async def claim(session, execution_id, *, kind, worker_id, now, lease_expires_at):
    # Must be the first statements of the caller's transaction.
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(text("SET LOCAL statement_timeout = '10s'"))
    eligible = (
        WorkflowExecution.id == execution_id,
        WorkflowExecution.status.in_(_CLAIMABLE[kind]),
        WorkflowExecution.lease_expires_at.is_(None),
        WorkflowExecution.lease_owner.is_(None),
    )
    locked = select(WorkflowExecution.id).where(*eligible).with_for_update(skip_locked=True).limit(1)
    row = (await session.execute(
        sa_update(WorkflowExecution)
        .where(WorkflowExecution.id == locked.scalar_subquery(), *eligible)
        .values(status="running", lease_owner=worker_id, lease_expires_at=lease_expires_at,
                running_since=now, delivery_attempts=WorkflowExecution.delivery_attempts + 1)
        .returning(WorkflowExecution.id, WorkflowExecution.variables)
        .execution_options(synchronize_session=False)
    )).one_or_none()
    return None if row is None else ClaimedExecution(id=row.id, variables=row.variables)
```

`fail_expired_approval_if_unchanged` compares `WorkflowExecution.variables == cast(literal(snapshot, JSONB), JSONB)` (JSONB equality, as Prisma's `equals`). `fail_expired_lease_if_unchanged` compares `lease_expires_at == :scanned` using the aware value read from the record (`UTCDateTime` round-trips milliseconds exactly).

- [x] **Repo tests first** (`@pytest.mark.integration`, real Postgres): create returns a full record (UUIDv7 id, `started_at` set) and `AlreadyExists(constraint="workflow_executions_workflow_id_user_id_idempotency_key_key")` on a same-user duplicate with the session still usable; two users same key both insert; NULL `user_id` rows never collide; `find_by_idempotency_key` scoping; `list_page` filters, count, order, offset; `list_refs`; `delete_terminal` deletes only terminal rows among the ids, plus exactly their events and checkpoint rows, returns what it deleted, cascades approvals, leaves a bystander; every guarded function: wins from its expected state, loses (returns False, row byte-identical) from every other status in `('queued','running','waiting_approval','completed','failed','canceled')`, and writes exactly its SET columns (snapshot all columns before/after); `claim` for each kind × each status × leased/unleased; `insert_if_absent` twice; `claim_orphans_by_email` idempotent and skips owned rows.
- [x] **Race tests** (`test_executions_races.py`, separate sessions from one sessionmaker, `asyncio.gather`, each repeated 10 times): 5 concurrent `claim` → exactly one `ClaimedExecution`, `delivery_attempts == 1`; one transaction holds the row lock (`SELECT … FOR UPDATE` in an open session) while `claim` runs → `claim` returns `None` without waiting (SKIP LOCKED, completes in < 1 s); `cancel_if_active` vs `mark_completed_if_running` → exactly one True, final status matches; `cancel_if_active` ×5 → one True; `fail_expired_lease_if_unchanged` vs `mark_completed_if_running` → one True; `fail_expired_lease_if_unchanged` with a stale `lease_expires_at` → False; `fail_orphaned_if_unchanged` after a claim set the lease → False; `fail_expired_approval_if_unchanged` with a stale snapshot → False; `mark_waiting_approval_if_running` vs `cancel_if_active` → one True.
- [x] Implement; run repo + race tests until green; pyright (CI parity) + ruff.
- [x] Commit: `feat(db): executions repository with guarded transitions (phase 4a)`.

---

### Task 3: Events, rate limiter and the WebSocket on SQLAlchemy

**Files:** Create `src/db/repos/events.py`, `src/db/repos/rate_limits.py`, `tests/integration/db/repos/test_events.py`, `tests/integration/db/repos/test_rate_limits.py`. Modify `src/engine/events_pg.py`, `src/security/rate_limit_pg.py`, `src/api/events_ws.py`, `src/storage/db.py` (remove lines 86–91 of `prisma_lifespan`: the event store and rate limiter imports and construction), `src/main.py` (lifespan body), `tests/integration/sqla_phase4a/test_events.py` (**only** `test_append_after_retention_gap_today_collides` → `test_append_after_retention_gap_continues_from_max`, asserting the returned seq is max + 1).

**Interfaces (produce):**

```python
# src/db/repos/events.py
@dataclass(frozen=True)
class EventRecord: id: str; execution_id: str; seq: int; type: str; payload: Any; created_at: datetime

async def append(session, *, execution_id: str, type: str, payload: Any) -> int:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"execution_events:{execution_id}"},
    )
    next_seq = select(func.coalesce(func.max(ExecutionEvent.seq), 0) + 1).where(
        ExecutionEvent.execution_id == execution_id
    ).scalar_subquery()
    return await session.scalar(
        insert(ExecutionEvent)
        .values(id=new_id(), execution_id=execution_id, seq=next_seq, type=type,
                payload=payload, created_at=utcnow())
        .returning(ExecutionEvent.seq)
    )
async def list_since(session, execution_id: str, *, after_seq: int) -> list[EventRecord]   # seq ASC
async def delete_for_executions(session, execution_ids: list[str]) -> int
async def delete_older_than(session, cutoff: datetime) -> int

# src/db/repos/rate_limits.py
@dataclass(frozen=True)
class BucketRecord: tokens: float; last_refill: datetime
async def lock_bucket(session, route_key: str, client_key: str) -> BucketRecord | None   # FOR UPDATE
async def insert_bucket(session, route_key: str, client_key: str, *, tokens: float, last_refill: datetime) -> None  # AlreadyExists
async def save_bucket(session, route_key: str, client_key: str, *, tokens: float, last_refill: datetime) -> None

# src/engine/events_pg.py  (name and methods kept: context.py, events_ws, executor use them)
class PostgresEventStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None
    async def append(self, event: ExecutionEvent) -> int          # own short session, committed before returning
    async def list_since(self, execution_id: str, *, after_seq: int) -> list[ExecutionEvent]

# src/security/rate_limit_pg.py
class PostgresRateLimiter:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None
    async def check(self, route_key: str, client_key: str, config: BucketConfig) -> tuple[bool, float]
```

`PostgresRateLimiter.check` in one short session: `lock_bucket`; if `None`, `insert_bucket(capacity - 1)` → commit → `(True, 0.0)`; on `AlreadyExists` (savepoint rolled back) `lock_bucket` again and consume; consume = today's refill formula; allowed → `save_bucket` + commit; denied → rollback (no write), `retry_after` as today.

`src/main.py` lifespan body, directly inside `async with prisma_lifespan(app) as db, sqlalchemy_lifespan(app):` and before `sync_llm_keys_from_db`:

```python
        sessionmaker = app.state.db_sessionmaker
        app.state.event_bus = PostgresEventStore(sessionmaker)
        app.state.rate_limiter = PostgresRateLimiter(sessionmaker)
```

`src/api/events_ws.py`: replace the `app.state.db` lookup with `sessionmaker = request_sessionmaker(ws)`; `None` → close 4500 `server error` (today's behaviour without `db`); execution read via `executions_repo.get` in a short session closed before `ws.accept`; `execution.user_id`/`execution.status` from the record; event reads stay `event_store.list_since` (each its own session). The dedicated asyncpg LISTEN connection and the 15-second wait are unchanged (D7); no SQLAlchemy session is open during `notified.wait()`.

- [x] Repo tests first: serial seqs 1..n; seq continues from max after deleting the lowest rows (D2 fix); **20 concurrent appends** for one execution → seqs exactly 1..20, no error, no gap; concurrent appends for two executions interleave independently; `list_since` order and cursor; `delete_older_than` boundary (strictly older); rate limits: lock/insert/save round trip, `AlreadyExists` on duplicate insert with session usable, **10 concurrent checks with 3 tokens → exactly 3 allowed** (D3), deny leaves `last_refill`.
- [x] Port; unit ordering tests: `PostgresEventStore.append`'s session exits before `LangGraphExecutor._emit` and `_persist_and_notify` call `notify_execution_event` (record `notify` as a network event); `events_ws` holds no session during the wait or a send (fake event store + `RecordingSessionmaker`; record `ws:wait`, `ws:send`).
- [x] Convert unit tests: `tests/unit/storage/test_db.py` and `tests/unit/test_main.py` (where they assert `prisma_lifespan` attaches `event_bus`/`rate_limiter`, assert the main lifespan does), `tests/unit/engine/test_events_pg.py`, `tests/unit/security/test_rate_limit_pg.py`, `tests/unit/api/test_events_ws.py` (patch `executions_repo.get`, `FakeSessionmaker`; `PostgresEventStore` over a fake store), `tests/unit/engine/test_events_wrapper.py` (event store part).
- [x] Verify: repo tests; `tests/integration/sqla_phase4a/test_events.py` and `test_rate_limit.py` (only the listed edit); `tests/integration/test_events_ws_hardening.py`; full unit suite; CI-parity pyright. Commit `feat(db): execution events and rate limits on SQLAlchemy (phase 4a)`.

---

### Task 4: Executor, cooperative stop, claim and the dev-mode fallback

**Files:** Modify `src/engine/langgraph_executor.py`, `src/engine/execution_control.py`, `src/engine/events_wrapper.py`, `src/engine/context.py`, `src/api/internal.py` (`claim_and_run_execution`, `claim_and_run`, `poll_file_triggers`' executor construction), `src/execution/cloud_tasks.py`, and the minimum in callers to keep them green: the `LangGraphExecutor(...)` constructions in `src/api/executions.py:102-107` and `src/api/run.py:79-83`, the `except UniqueViolationError` at `src/api/executions.py:179` and `src/api/run.py:158` → `except AlreadyExists` (start_execution now raises it), and `_to_execution_read(record)` (interface in Task 5, defined here) used for the freshly created row at `src/api/executions.py:263` (do not rely on `ExecutionRead.model_validate` reading snake_case attributes). Update the ADAPTER AREA of `tests/integration/sqla_phase3/conftest.py` (`Phase3Adapter._executor`, `run_execution`, `run_after_workflow_deleted`, `prepare_compiled_for`) and of `tests/integration/sqla_phase4a/conftest.py`.

**Interfaces (produce):**

```python
class LangGraphExecutor:
    def __init__(self, *, sessionmaker: async_sessionmaker[AsyncSession],
                 checkpointer: BaseCheckpointSaver[Any],
                 event_bus: PostgresEventStore | None = None) -> None
    async def start_execution(self, *, workflow_id: str, input: Any, user_id: str | None,
                              idempotency_key: str | None = None) -> ExecutionRecord   # commits; AlreadyExists
    async def run(self, execution_id: str) -> None
    async def resume(self, execution_id: str, decision: str) -> None

# src/engine/execution_control.py
async def ensure_execution_running(sessionmaker: async_sessionmaker[AsyncSession], execution_id: str) -> None

# src/engine/context.py (replaces set_current_db / get_current_db)
def set_current_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None
def get_current_sessionmaker() -> async_sessionmaker[AsyncSession] | None

# src/api/internal.py
async def claim_and_run_execution(execution_id: str, kind: Literal["run", "resume"],
                                  sessionmaker: async_sessionmaker[AsyncSession],
                                  checkpointer: Any, event_bus: Any, *, worker_id: str) -> dict[str, str]
```

Executor rules: `_load_execution` → `executions_repo.get` in a short session; `_prepare_compiled` reads the workflow (already a short session) and uses `execution.workflow_id`/`input`/`user_id`/`thread_id`; `set_current_sessionmaker(self.sessionmaker)`; each `_mark_*` opens a short session, calls its guarded function with `now=utcnow()`, commits, returns the bool to `_transition_succeeded(…, 1 if won else 0)` (keep the log line). No session is open during `compiled.ainvoke`, `aget_state`, `send_approval_email` or `_emit`.

`claim_and_run_execution`:

```python
    claim_time = utcnow()
    lease_expires = claim_time + timedelta(seconds=get_settings().execution_lease_seconds)
    async with asyncio.timeout(10), sessionmaker() as session:   # today: db.tx(timeout=10000)
        claimed = await executions_repo.claim(
            session, execution_id, kind=kind, worker_id=worker_id,
            now=claim_time, lease_expires_at=lease_expires,
        )
        await session.commit()
    if claimed is None:
        return {"status": "already_claimed"}
    # ... unchanged try/except; resume decision = (claimed.variables or {}).get("_resume_decision")
    # crash handler: async with sessionmaker() as s: won = await executions_repo.mark_failed_if_running(
    #     s, execution_id, error=f"{type(exc).__name__}: {exc}", failure_code="execution_error", now=utcnow());
    #     await s.commit()  -> updated_count = 1 if won else 0 (None if the write raised, as today)
```

The `claim_and_run` route takes `sessionmaker = require_sessionmaker(request)` instead of `db`. `cloud_tasks._run_in_process(execution_id, kind, db)` builds `PrismaCheckpointSaver(db)` (phase 5) and `PostgresEventStore(get_registered_sessionmaker())`, raising `RuntimeError` (logged by its existing handler) when no sessionmaker is registered; `enqueue_execution`'s docstring says `db` now only feeds that checkpointer (`# TODO(phase 5)`).

- [x] Unit ordering tests first (`tests/unit/engine/test_langgraph_executor.py`, `tests/unit/api/test_internal.py`): with `RecordingSessionmaker` and a fake compiled graph recording `graph:ainvoke`/`graph:aget_state`, a fake `send_approval_email` recording `email`, and a fake event bus recording `emit`: no such event inside a session, for run → completed, run → waiting_approval, run → failed, resume → completed. Claim: the session that ran `claim` exited (committed) before the fake executor's `run`; `asyncio.timeout(10)` wraps it (a hanging fake `claim` raises `TimeoutError` within the bound).
- [x] Port; `require_same_caller` is NOT changed in this task (still reads Prisma rows from `_idempotency`; Task 5 moves both together).
- [x] Convert unit tests: `test_langgraph_executor.py`, `test_events_wrapper.py` (stop checks), `tests/unit/engine/test_context.py`, `test_internal.py`, `test_internal_fail_closed.py`, `test_internal_poll_file_triggers.py`, `tests/unit/execution/test_cloud_tasks.py`, and the executor construction in `test_executions*.py` / `test_run_external.py`.
- [x] Convert `tests/integration/test_durable_execution.py` (setup via SQLAlchemy `Db` helpers instead of `app.state.db`; the sweep seam becomes a monkeypatch of `executions_repo.list_*`; every assertion kept) and `tests/integration/test_events_ws_hardening.py` (`claim_and_run(payload=…, request=…, _oidc=None)` without `db`).
- [x] Verify: oracle groups 6, 7, 12 and the phase 3 oracle (`tests/integration/sqla_phase3`, adapter-only edits); `test_durable_execution.py`, `test_user_approval_approved.py`, `test_user_approval_rejected.py`, `test_start_to_end.py`, `test_while_countdown.py`, `test_if_else_routing.py`; full unit suite; CI-parity pyright. Commit `feat(db): executor and work claim on SQLAlchemy (phase 4a)`.

---

### Task 5: Execution routes, idempotency and external invoke

**Files:** Modify `src/api/executions.py` (create, list, get, delete, bulk delete, cancel; NOT resume), `src/api/_idempotency.py`, `src/api/run.py`.

**Interfaces (produce):**

```python
# src/api/_idempotency.py
async def find_execution_by_idempotency_key(session: AsyncSession, workflow_id: str, user_id: str,
                                            idempotency_key: str) -> ExecutionRecord | None
def require_same_caller(existing: ExecutionRecord, user_id: str) -> None   # compares existing.user_id

# src/api/executions.py
def _to_execution_read(record: ExecutionRecord) -> ExecutionRead   # explicit field mapping
```

Route rules:
- **create:** the workflow read and the idempotency lookup share one short session closed before `start_execution`; `start_execution` commits its own row; then `enqueue_execution`; on enqueue failure a fresh short session runs `mark_enqueue_failed_if_queued` and commits (inner exception still logged and swallowed); the `AlreadyExists` branch re-reads in a short session and keeps the 409 `idempotency key already used for this workflow` when no own row exists. Ordering test: no session open during `enqueue_execution`.
- **list/get:** one short session; `owner_id=None` for admins.
- **delete / bulk delete:** authz read and `delete_terminal` in **one** transaction (D6) with one commit; single delete keeps its 404/409 checks before deleting; bulk keeps `skippedCount = max(0, len(requested) - len(deleted))` for explicit ids and 0 for `allInScope`.
- **cancel:** read + `cancel_if_active` in one short session and one commit; response is `_to_execution_read(dataclasses.replace(row, status="canceled", error=_CANCEL_ERROR_MESSAGE))` (D9).
- **resume (4b):** untouched; keeps `db: Prisma` and `ExecutionRead.model_validate(<Prisma row>)`.
- **run.py:** slug read (already a short session), idempotency lookup in a short session, `start_execution`, enqueue; the sync poll opens a short session per iteration (`executions_repo.get`), closed before `asyncio.sleep(0.25)`. Ordering test.

- [x] Port; convert unit tests (`tests/unit/api/test_executions.py`, `test_executions_list.py`, `test_executions_size_limit.py`, `test_run_external.py`, `tests/unit/security/test_hardening.py` execution parts) to patched repo functions + `override_session` / `FakeSessionmaker`; `test_executions_resume.py` stays Prisma-shaped (4b).
- [x] Verify: oracle groups 1–5, 10, 11, 12; phase 1–3 oracles; `test_external_invoke.py`, `test_security_hardening.py`, `test_workflow_crud.py`; full unit suite; CI-parity pyright. Commit `feat(db): execution routes and idempotency on SQLAlchemy (phase 4a)`.

---

### Task 6: Sweeper, builder diagnosis and migration CLI

**Files:** Modify `src/maintenance/execution_sweeper.py`, `src/api/internal.py` (`sweep`), `src/main.py` (`start_sweeper(app, app.state.db_sessionmaker, …)`), `src/builder/diagnostics.py`, `src/builder/builder_agent.py`, `src/api/builder.py` (drop `db: Prisma = Depends(get_db)` and its TODO), `src/migration/writer.py`, `src/migration/runner.py`, `src/migration/reconcile.py`. If D4 is approved: `tests/integration/sqla_phase4a/test_diagnosis.py` (**only** `test_diagnosis_today_ignores_owner` → `test_diagnosis_only_for_own_execution_of_this_workflow`, plus an admin case).

**Interfaces (produce):**

```python
# src/maintenance/execution_sweeper.py (signatures; bodies keep today's logic and messages)
async def sweep_orphaned_running(sessionmaker, *, grace_seconds: int, now: datetime | None = None) -> SweepResult
async def sweep_expired_approvals(sessionmaker, *, timeout_hours: int, now: datetime | None = None) -> SweepResult
async def sweep_expired_leases(sessionmaker, *, now: datetime | None = None) -> SweepResult
async def sweep_old_execution_events(sessionmaker, *, retention_days: int, now: datetime | None = None) -> int
def start_sweeper(app: FastAPI, sessionmaker, *, interval_seconds: int,
                  orphaned_running_grace_seconds: int, approval_timeout_hours: int) -> asyncio.Task[None] | None

# src/builder/diagnostics.py
async def build_diagnosis(sessionmaker, execution_id: str, *, workflow_id: str, user_id: str,
                          role: str) -> Diagnosis | None    # D4 scoping; like-for-like ignores the last three

# src/migration
async def upsert_execution(session: AsyncSession, data: dict[str, Any]) -> bool   # strict camelCase→column map
async def reconcile_by_email(session: AsyncSession, email: str) -> dict[str, int]  # no Prisma argument
```

Sweeper rules: candidates in one short session; each row's guarded write in its own short session + commit (per-row error isolation, `errored` counting and log lines as today); `marked += 1 if won else 0`; `sweep_old_execution_events` is one `delete_older_than` + commit. `run_builder_turn(…, sessionmaker=…, viewer=…)` calls `build_diagnosis` before `_tool_loop`, in its own short session (ordering test: no session during the LLM loop). The runner drops `Prisma()` and writes executions per row with commit/rollback like the other tables; input/output `None` stays SQL NULL (today's `_wrap_optional` drop).

- [x] Port; convert unit tests (`tests/unit/maintenance/test_execution_sweeper.py`, `tests/unit/builder/test_diagnostics.py`, builder route tests that pass `db`, `tests/unit/migration/test_reconcile.py`, `test_runner_writer.py`) and `tests/integration/test_migration.py` (reconcile signature; commit after reconcile, as in phase 3).
- [x] Verify: oracle groups 9, 13; `test_durable_execution.py`; `test_migration.py`; `test_builder_live.py` is live-only (skip); full unit suite; CI-parity pyright. Commit `feat(db): sweeper, builder diagnosis and migration CLI on SQLAlchemy (phase 4a)`.

---

### Task 7: Lock, docs, CI, PR

- [x] Grow `MIGRATED_PATHS` (`tests/unit/db/test_prisma_import_boundary.py`) with every file now free of `prisma`/`src.storage` imports; expected: `src/api/_idempotency.py`, `src/api/events_ws.py`, `src/api/builder.py`, `src/builder`, `src/engine` (verify `checkpointer` imports: `src/engine/langgraph_executor.py` must not import `src.storage`), `src/maintenance`, `src/migration`, `src/security/rate_limit_pg.py`. Exclusions with reasons in the comment: `src/api/executions.py` and `src/api/approval_email.py` (4b), `src/api/run.py` and `src/api/internal.py` (`get_db`/`get_checkpointer` for the enqueue fallback and checkpointer, phase 5), `src/execution/cloud_tasks.py` (`PrismaCheckpointSaver`, phase 5), `src/storage` (phase 5/6).
- [x] Done-criterion greps: `grep -rnE "\b(db|self\.db)\.(workflowexecution|executionevent|ratelimitbucket)\." src` → exactly the eight 4b lines; `grep -rn "query_raw\|execute_raw\|\.tx(" src` → empty; `grep -rn "UniqueViolationError" src` → only `src/storage` (if any) and 4b files.
- [x] CHANGELOG (`[Unreleased]`, first entry): executions (except resume), events and rate limits on SQLAlchemy; behaviour proven by `tests/integration/sqla_phase4a`; deliberate changes (D2 event sequence fix — both bugs, found in `src/engine/events_pg.py`; D3 atomic rate limiter; D4 builder diagnosis scoping if approved; D5 single-statement claim; D6 atomic delete; short sessions around the graph run, enqueue, sync poll and WebSocket); kept-as-is items (D9); follow-ups.
- [x] `docs/architecture.md` data-access section (guarded transitions, claim, event sequence lock); spec §3.3 phase 3 "(done, PR #33)" and phase 4 "(4a done, PR pending; 4b next)"; this plan's checkboxes and an Outcome section.
- [x] Record pool checkout waits (D10) from the full suite; note them in Outcome.
- [x] Run everything CI runs with a throwaway Postgres 15 (`docker run -d --rm --name composer-p4a-pg -e POSTGRES_PASSWORD=pg -p 55433:5432 postgres:15`, then `prisma migrate deploy` + `alembic upgrade head`): `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests && uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests && uv run pytest && uv run pytest -m "integration and not live"`. Delete the container when the phase is done.
- [ ] Commit; push; open the PR. **4a ships combined with 4b in one PR** (Balaji's decision; see the [4b plan](2026-11-24-sqlalchemy-phase-4b-approvals-and-resume.md)); wait for all checks; merge only with Balaji's go-ahead (merging deploys to production). After that combined deploy: 24 h of clean backend logs before phase 5 starts (spec §5.1), plus one real run that pauses at an approval and resumes, once in-app and once via the emailed link.

## Phase 4b scope (done: [4b plan](2026-11-24-sqlalchemy-phase-4b-approvals-and-resume.md))

- **Tables/paths:** `approvals` (all writes), `POST /executions/{id}/resume` (`src/api/executions.py:562-673`: read, G3, `approval.create`, `variables` stamp, enqueue), `GET /approvals/email/{token}` and `POST /approvals/email/{token}/confirm` (`src/api/approval_email.py:66-215`: read, G4, `approval.create`, `variables` stamp, enqueue).
- **Repo additions:** `executions.resume_if_waiting(session, id, *, decision, now) -> ClaimedExecution | None` — recommend folding the `_resume_decision` stamp into the same guarded UPDATE (`variables = variables || jsonb_build_object('_resume_decision', :d)`), closing today's window where a redelivered resume task can claim the row before the decision is stamped (it would 422 and leave a lease); `approvals.create(session, …)` in the same transaction; one commit before `enqueue_execution`.
- **Oracle:** extend `tests/integration/sqla_phase4a/test_resume_crossover.py` into a full 4b group (double resume race → one 200 / one 409; email confirm race; stale `pending_since` token; scanner GET never mutates; approval rows' exact columns incl. `via_email_link`, `approver_email`, `note`).
- **Then:** `executions.py` and `approval_email.py` leave Prisma; `ExecutionRead` drops Prisma-row support; `tests/unit/api/test_executions_resume.py` and `test_approval_email.py` convert; the remaining `get_db` users are only the phase 5 checkpointer plumbing.

## Follow-ups (not in this phase)

- Cancel's response returns stale `completedAt: null`; return the updated row (RETURNING) instead.
- Resume-kind claim without `_resume_decision` leaves a fresh lease until the lease sweeper fails it; fail it at once (4b's single-UPDATE stamp removes the main cause).
- `POST /api/run/{slug}` leaves a row `queued` when the enqueue raises; mark it failed as `POST /executions` does.
- `list_executions` has no tiebreak; add `id DESC` (UUIDv7 ids are time-ordered).
- `sweep_expired_approvals` loads every `waiting_approval` row and filters in Python; filter `variables->>'_pending_approval_since' < :cutoff` in SQL.
- Claim timeout window: if `asyncio.timeout(10)` fires after the server commits the claim but before the reply is read, the row stays leased, the handler answers 500, the Cloud Tasks redelivery gets `already_claimed`, and the row fails at lease expiry (`lease_expired`). No duplicate execution; same window as the old Prisma `tx(timeout=10000)`.
- **Phase 5 LISTEN/NOTIFY design note:** LISTEN on a dedicated, non-pooled `psycopg.AsyncConnection.connect(url, autocommit=True, prepare_threshold=None)` per WebSocket, `LISTEN composer_execution_events`, then `async for n in conn.notifies(timeout=15.0)` in place of the asyncpg listener; it must use a direct (non-pooler) Neon host, since LISTEN does not work through a transaction-mode pooler. Send NOTIFY with `SELECT pg_notify(…)` inside the append transaction (delivered only on commit, so ordering holds by construction) and drop `events_notify.py`'s asyncpg connection.


## Outcome

What changed while executing this plan:

- **Rebased onto main** (PRs #36 to #39). #37's builder fix was already cherry-picked here, so that commit was dropped as already applied; no code conflicts. Alembic reaches `0003_assignment_user_fks`; 4a adds no revision.
- **Oracle:** 143 tests in `tests/integration/sqla_phase4a` (extended after review), green on the Prisma code first and unchanged on the port apart from the adapter bodies. Three tests that pinned today's bugs were flipped on purpose: the retention-gap event sequence (D2), the over-admitting rate limiter (D3) and the non-atomic delete (D6). A timestamp-format flake in the oracle helper (pydantic omits a zero fraction) was fixed.
- **Id lists use one array parameter** (`= ANY(:ids)`), not one bind per id: Postgres rejects more than 65,535 parameters and Prisma chunked, so a large admin bulk delete would have failed.
- **Pause-versus-cancel invariant:** the pause write is guarded on `running`, so when a cancel wins no approval email and no `approval_required` event is sent (pinned by the oracle).
- **Cloud Tasks dev-mode fallback:** a missing sessionmaker is now raised inside the existing handler and logged, instead of killing the fire-and-forget task silently (found while porting).
- **Event-wrapper unit tests restored:** the Task 4 rewrite had dropped three, including the only unit guard that a broken event store cannot abort a node or lose its output; they were restored.
- **503s without a sessionmaker:** `/internal/sweep` and `/internal/claim-and-run` answer 503 `database unavailable`, and the WebSocket closes 4500 before auth (database-less test apps only).
- **Small behaviour changes found in review:** a single delete that loses a race answers 204 (was 500); `_to_execution_read` maps null `node_results`/`variables` to `{}`; reconcile is atomic across workflows, executions and MCP servers.
- **`MIGRATED_PATHS`** gained `src/api/_idempotency.py`, `events_ws.py`, `builder.py`, `src/builder`, `src/engine`, `src/maintenance`, `src/migration` and `src/security/rate_limit_pg.py`. Exclusions: `executions.py` and `approval_email.py` (4b), `run.py` and `internal.py` and `cloud_tasks.py` (phase 5), `src/storage`.
- **Greps:** the execution/event/rate-limit grep returns six lines (all in `executions.py` and `approval_email.py`); adding `approval` returns the eight 4b lines. `query_raw|execute_raw|.tx(` and `UniqueViolationError` return nothing in `src`.
- **Review leftovers closed:** create-route ordering test, 404-caller-with-key never looks the key up, dry run never opens a session, diagnose turn has no session open when the model runs, migration column map names real columns.
- **Verification (local, throwaway Postgres 15 `composer-p4-pg`; CI minutes were exhausted):** oracle 4a plus phase 3 = 289 passed; unit 2256 passed; `-m "integration and not live"` = 676 passed, 1 skipped (Jira needs credentials); ruff, ruff format and pyright (default and Linux/3.12) clean.
- **D10 pool measurement** (full integration run, `QueuePool._do_get` timed by a temporary plugin, not committed; the figure includes opening new connections): 11,976 checkouts, p95 32.6 ms, p99 111 ms, max 2,012 ms, 163 over 100 ms, 2 over 1 s, one `ConnectionTimeout` in the whole run (the test suite has tests that exercise timeouts deliberately; the run passed). No claim `TimeoutError` surfaced. Defaults (`db_pool_size=2`, `db_max_overflow=3`) are kept. Production waits are still to be read from the staging deploy.
- **Follow-up:** rate-limit bucket row locks hold a pool connection while waiting; add `SET LOCAL lock_timeout` to the limiter transaction if pool waits show up in production.
