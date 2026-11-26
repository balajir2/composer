# Telemetry Phase 5.1 Durable Processing Attempts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist immutable processing-attempt history for every successfully claimed execution so later owner/admin APIs and UI can explain pauses, failures, leases, resumes, and recovery without inventing history for legacy rows.

**Architecture:** `workflow_executions` remains the durable thread and current summary. A new Alembic-managed `execution_attempts` table stores append-oriented attempts opened only by a winning claim and closed in the same transaction as a winning pause, cancellation, terminal, or lease-sweeper transition. The persisted attempt ID fences worker-owned writes so a delayed worker cannot mutate a later resumed attempt; an exact pending approval reference bridges the committed resume decision to the later claim.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL 15+, pytest/pytest-asyncio, Ruff, Pyright.

**Spec:** `docs/superpowers/plans/2026-11-20-telemetry-roadmap.md` Phase 5.1, refined by `docs/superpowers/notes/2026-11-25-codex-handoff-telemetry-phase-5.md` against merged main `77e5e80`.

## Global Constraints

- Start from `77e5e80`; do not revive Prisma models, migrations, or queries.
- Do not modify `src/storage/**`, checkpointer wiring in `src/engine/langgraph_executor.py`, `src/execution/cloud_tasks.py` dev fallback, `src/engine/events_notify.py`, or `src/api/events_ws.py`; those belong to Claude's SQLAlchemy Phase 5.
- Callers own transactions. Attempt writes occur in the same transaction as their guarded execution transition and only after that transition wins.
- Never hold a database session across a graph run, LLM/tool call, Cloud Tasks enqueue, email, or WebSocket wait.
- A successful `resume_if_waiting` records authorization/provenance but does not open an attempt; only the later winning `claim(kind="resume")` opens it.
- Cancellation is terminal. A cancellation while waiting for approval must not rewrite the already closed paused attempt.
- Preserve Phase 4 race behavior: resume and cancel may both win sequentially, with canceled as the final summary; the invariant is at most one attempt per winning claim, not at most one lifecycle transition.
- Do not backfill fictional attempt history. A legacy execution has `history_available=false` until its first real post-migration attempt exists.
- Metric/log attributes remain privacy-safe: free-form approval notes, emails, exception text, and payloads are not copied into attempt fields.
- Use bounded Alembic locks (`lock_timeout='5s'`, `statement_timeout='60s'`) and no destructive production migration workflow.

---

### Task 1: Define the Alembic schema and SQLAlchemy model

**Files:**
- Create: `src/db/migrations/versions/0005_execution_attempts.py`
- Create: `src/db/models/execution_attempts.py`
- Modify: `src/db/models/executions.py`
- Modify: `src/db/models/__init__.py`
- Modify: `tests/unit/db/test_models_metadata.py`
- Create: `tests/integration/db/test_alembic_0005_execution_attempts.py`

**Interfaces:**
- Produces: `ExecutionAttempt`, `AttemptResult`, `AttemptTriggerKind`, and nullable `WorkflowExecution.pending_attempt_approval_id`.
- Consumes: `Base`, `id_column`, `created_at_column`, `UTCDateTime`, `WorkflowExecution`, `Approval`, and Alembic revision `0004_langgraph_saver_tables`.

- [ ] **Step 1: Write failing metadata tests**

  Add `execution_attempts` to the exact table set and assert these named relationships and indexes:

  ```python
  expected_attempt_indexes = {
      "execution_attempts_execution_id_ordinal_key",
      "execution_attempts_one_running_per_execution_key",
      "execution_attempts_started_at_id_idx",
      "execution_attempts_result_started_at_id_idx",
      "execution_attempts_recovery_source_attempt_id_idx",
      "execution_attempts_approval_id_idx",
  }
  assert expected_attempt_indexes <= {
      index.name for index in Base.metadata.tables["execution_attempts"].indexes
  }
  ```

  Assert FKs: attempt execution → execution `CASCADE/CASCADE`, approval → approval `SET NULL`, recovery source → attempt `SET NULL`, and workflow execution pending approval → approval `SET NULL`.

- [ ] **Step 2: Run the metadata tests and verify they fail**

  Run: `.venv\Scripts\python -m pytest tests/unit/db/test_models_metadata.py -q -o addopts=''`

  Expected: FAIL because `execution_attempts` and the pending approval column do not exist.

- [ ] **Step 3: Write migration tests before the migration**

  In a dedicated test database, downgrade explicitly to `0004_langgraph_saver_tables`, seed queued/running/waiting/terminal legacy executions, upgrade to `0005_execution_attempts`, and assert:

  - legacy summaries are unchanged and `execution_attempts` is empty;
  - invalid ordinal (`0`), result, trigger kind, open attempt with `ended_at`, and `ended_at < started_at` are rejected;
  - `(execution_id, ordinal)` is unique and only one row per execution may have `ended_at IS NULL`;
  - deleting an execution cascades attempts; deleting an approval nulls `approval_id` and `pending_attempt_approval_id`;
  - downgrade/upgrade round-trip works on an empty attempt table;
  - catalog inspection verifies the partial-index predicate `ended_at IS NULL`.

- [ ] **Step 4: Implement revision `0005_execution_attempts`**

  Create an empty table with no backfill:

  ```text
  id text primary key
  execution_id text not null
  ordinal integer not null
  started_at timestamp(3) without time zone not null default CURRENT_TIMESTAMP
  ended_at timestamp(3) without time zone null
  result text not null
  failure_code text null
  lease_owner text not null
  lease_expires_at timestamp(3) without time zone not null
  trigger_kind text not null
  recovery_source_attempt_id text null
  approval_id text null
  actor_user_id text null
  reason_ref text null
  ```

  Add named checks for `ordinal > 0`, allowed result values (`running`, `paused`, `completed`, `failed`, `canceled`), allowed trigger values (`run`, `resume`, `recovery`), `(result = 'running') = (ended_at IS NULL)`, and `ended_at IS NULL OR ended_at >= started_at`. Add `pending_attempt_approval_id` to `workflow_executions` after creating the attempt table; its FK targets `approvals.id` with `ON DELETE SET NULL`.

- [ ] **Step 5: Implement the model and controlled enums**

  Use Python `StrEnum` values backed by `Text` plus the same named CHECK constraints as the migration. Define all indexes in `__table_args__`; the open-attempt index uses `postgresql_where=text("ended_at IS NULL")`. Export the model and enums from `src/db/models/__init__.py`.

- [ ] **Step 6: Run schema tests**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/unit/db/test_models_metadata.py -q -o addopts=''
  .venv\Scripts\python -m pytest tests/integration/db/test_alembic_0005_execution_attempts.py -q --no-cov
  .venv\Scripts\python -m pytest tests/integration/db/test_schema_parity.py -q --no-cov
  ```

- [ ] **Step 7: Commit**

  Commit: `feat(execution): define durable processing attempts`

---

### Task 2: Add the attempt repository and history queries

**Files:**
- Create: `src/db/repos/execution_attempts.py`
- Create: `tests/integration/db/repos/test_execution_attempts.py`

**Interfaces:**
- Produces:
  - frozen `AttemptRecord` containing every attempt column;
  - `open_for_claim(session, *, execution_id, started_at, lease_owner, lease_expires_at, trigger_kind, approval_id, actor_user_id, recovery_source_attempt_id=None, reason_ref=None) -> AttemptRecord`;
  - `close_open(session, *, execution_id, attempt_id, result, failure_code, ended_at) -> bool`;
  - `get(session, attempt_id) -> AttemptRecord | None`;
  - `has_history(session, execution_id) -> bool`;
  - `list_page(session, *, execution_id, limit, offset) -> tuple[list[AttemptRecord], int]`;
  - `list_operational_page(session, *, result, started_after, started_before, limit, offset) -> tuple[list[AttemptRecord], int]`.
- Consumes: the Task 1 model; caller already holds the execution-row lock before `open_for_claim`.

- [ ] **Step 1: Write repository tests for open/close and rollback**

  Test ordinal 1 regardless of legacy `delivery_attempts`, frozen detached records, exact timestamps/lease/provenance, and `close_open` preserving the historical lease. Roll back after open and after close, then verify both summary and history from a fresh session. Inject an attempt insert failure and prove the caller can roll back without partial history.

- [ ] **Step 2: Write query and pagination tests**

  Assert execution isolation, ordinal ascending order with ID tie-break, total counts, first/final/empty pages, result/time filters, and deterministic global order `(started_at DESC, id DESC)`. Reject `limit < 1`, `limit > 100`, and negative offsets with `ValueError`.

- [ ] **Step 3: Run tests and verify they fail**

  Run: `.venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempts.py -q --no-cov`

- [ ] **Step 4: Implement the minimal repository**

  `open_for_claim` calculates `coalesce(max(ordinal), 0) + 1` only after the caller's winning claim has locked the execution row. It never commits. `close_open` updates exactly `id`, `execution_id`, and `ended_at IS NULL`, and returns whether one row won. Validate controlled enum inputs through typed signatures, not arbitrary strings.

- [ ] **Step 5: Run repository tests and static checks**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempts.py -q --no-cov
  .venv\Scripts\python -m ruff check src/db/repos/execution_attempts.py tests/integration/db/repos/test_execution_attempts.py
  .venv\Scripts\python -m pyright src/db/repos/execution_attempts.py
  ```

- [ ] **Step 6: Commit**

  Commit: `feat(execution): add attempt history repository`

---

### Task 3: Open attempts atomically on winning claims

**Files:**
- Modify: `src/db/repos/executions.py`
- Modify: `src/api/internal.py`
- Modify: `tests/unit/api/test_internal.py`
- Create: `tests/integration/db/repos/test_execution_attempt_claims.py`

**Interfaces:**
- Produces: `ClaimedExecution(id, variables, attempt_id, attempt_ordinal)` and a committed attempt identity passed to the executor.
- Consumes: Task 2 `open_for_claim`; `WorkflowExecution.pending_attempt_approval_id` is captured and cleared in the claim transaction.

- [ ] **Step 1: Write failing claim tests**

  Prove a winning initial claim creates ordinal 1 with trigger `run`; a winning post-approval claim creates the next ordinal with trigger `resume` and the exact pending approval ID. Wrong-kind, locked, missing, terminal, or already-leased claims create no attempt. A claim rollback removes both the lease/delivery increment and attempt.

- [ ] **Step 2: Write deterministic concurrent-claim tests**

  Run five independently committed sessions against the same initial and resume claim. Assert one `ClaimedExecution`, one open attempt, one ordinal, and one delivery increment. Seed `delivery_attempts=7` before the first genuine claim and assert ordinal 1.

- [ ] **Step 3: Run tests and verify failure**

  Run: `.venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_claims.py -q --no-cov`

- [ ] **Step 4: Extend `claim` inside its existing transaction**

  After the guarded execution UPDATE returns a row, call `open_for_claim` before commit. Use `kind` as the trigger, carry `pending_attempt_approval_id` into the attempt, and clear that bridge before returning. Any insert/clear failure rolls back the claim. Do not use `delivery_attempts` as the ordinal.

- [ ] **Step 5: Thread the committed attempt ID into worker calls**

  Change internal dispatch to call `executor.run(execution_id, attempt_id=claimed.attempt_id)` or `executor.resume(execution_id, decision, attempt_id=claimed.attempt_id)` only after the claim transaction commits. Bind the same ID in telemetry correlation. Do not change checkpointer construction or Cloud Tasks fallback plumbing.

- [ ] **Step 6: Run focused and Phase 4 oracle tests**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_claims.py tests/unit/api/test_internal.py -q --no-cov
  .venv\Scripts\python -m pytest tests/integration/sqla_phase4a/test_claim.py -q --no-cov
  ```

- [ ] **Step 7: Commit**

  Commit: `feat(execution): open attempts on durable claims`

---

### Task 4: Fence worker outcomes and close attempts atomically

**Files:**
- Modify: `src/db/repos/executions.py`
- Modify: lifecycle methods only in `src/engine/langgraph_executor.py`
- Modify: `src/api/internal.py`
- Modify: `tests/unit/engine/test_langgraph_executor.py`
- Modify: `tests/unit/api/test_internal.py`
- Create: `tests/integration/db/repos/test_execution_attempt_transitions.py`

**Interfaces:**
- Produces worker transitions requiring `attempt_id`: pause, complete, fail, and crash-fail; each transition fences the execution update to that currently open attempt and closes it in the same transaction.
- Consumes: Task 2 `close_open`, Task 3 committed attempt ID.

- [ ] **Step 1: Write failing transition tests**

  Test `running → paused/completed/failed` with exact end time/result/failure code; repeat/losing calls leave history byte-for-byte unchanged. Test transaction rollback restores both running summary and open attempt.

- [ ] **Step 2: Write the stale-worker regression test**

  Create attempt 1, pause it, resume and claim attempt 2, then submit completion/failure/pause using attempt 1. Assert the stale write loses, attempt 2 remains open, and summary remains owned by attempt 2. The same operation with attempt 2 must win.

- [ ] **Step 3: Run tests and verify failure**

  Run: `.venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_transitions.py -q --no-cov`

- [ ] **Step 4: Add attempt fencing to worker-owned repository transitions**

  Each worker transition first guards the summary UPDATE with an `EXISTS` predicate for the supplied open attempt ID. Only after the summary UPDATE returns a winner does `close_open` run. Add `now` to pause so the attempt has an exact end time. If closure unexpectedly loses after the guarded summary wins, raise and roll back rather than commit inconsistent state.

- [ ] **Step 5: Update executor lifecycle signatures only**

  Carry `attempt_id` through `run`, `resume`, `_execute`, `_mark_completed`, `_mark_waiting_approval`, and `_mark_failed`. Preserve all checkpointer setup and graph invocation code. The internal crash handler uses the claimed attempt ID. `CancelledError` remains lease-sweeper-owned and is not converted to immediate failure.

- [ ] **Step 6: Run focused lifecycle and oracle tests**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_transitions.py tests/unit/engine/test_langgraph_executor.py tests/unit/api/test_internal.py -q --no-cov
  .venv\Scripts\python -m pytest tests/integration/sqla_phase4a -q --no-cov
  ```

- [ ] **Step 7: Commit**

  Commit: `feat(execution): fence and close worker attempts`

---

### Task 5: Attach approval provenance and cancellation outcomes

**Files:**
- Modify: `src/db/repos/executions.py`
- Modify: `src/api/executions.py`
- Modify: `src/api/approval_email.py`
- Modify: `tests/unit/api/test_executions.py`
- Modify: `tests/unit/api/test_approval_email.py`
- Create: `tests/integration/db/repos/test_execution_attempt_resume_cancel.py`

**Interfaces:**
- Produces: exact `pending_attempt_approval_id` persisted by the winning resume transaction; running cancellation closes its open attempt, while queued/waiting cancellation creates or rewrites no attempt.
- Consumes: `approvals_repo.create(...) -> ApprovalRecord`, Task 2 `close_open`.

- [ ] **Step 1: Write failing approval bridge tests**

  For in-app and email confirmation, assert the winning transaction stores the exact returned Approval ID in `pending_attempt_approval_id`; losing concurrent decisions roll back their approval row. Resolution alone creates no attempt. The subsequent resume claim consumes the bridge and places the exact ID on the attempt.

- [ ] **Step 2: Write cancellation race tests**

  Cover cancel-before-claim (zero attempts), claim-before-cancel (one canceled attempt), pause-then-cancel (paused attempt remains paused), cancel-before-pause (attempt closes canceled and pause loses), and resume-then-cancel (both may win sequentially; no resume attempt opens without a claim).

- [ ] **Step 3: Run tests and verify failure**

  Run: `.venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_resume_cancel.py -q --no-cov`

- [ ] **Step 4: Persist the approval bridge atomically**

  Create the Approval within the same transaction and pass its ID to `resume_if_waiting`; on a winning resume, store it in `pending_attempt_approval_id`. On a losing resume, roll back the newly created Approval. For in-app resume, pass `PendingInstance` from the row read so an obsolete pause cannot authorize a newer pause instance.

- [ ] **Step 5: Close only a genuinely open attempt on cancellation**

  After `cancel_if_active` wins, close the current open attempt as `canceled` if one exists. Queued and waiting-approval cancellation succeeds without fabricating history; an already paused attempt stays paused.

- [ ] **Step 6: Run Phase 4b and focused tests**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_resume_cancel.py tests/unit/api/test_executions.py tests/unit/api/test_approval_email.py -q --no-cov
  .venv\Scripts\python -m pytest tests/integration/sqla_phase4b -q --no-cov
  ```

- [ ] **Step 7: Commit**

  Commit: `feat(execution): preserve attempt approval and cancel history`

---

### Task 6: Close attempts from sweepers without inventing history

**Files:**
- Modify: `src/db/repos/executions.py`
- Modify: `src/maintenance/execution_sweeper.py`
- Modify: `tests/unit/maintenance/test_execution_sweeper.py`
- Create: `tests/integration/db/repos/test_execution_attempt_sweepers.py`

**Interfaces:**
- Produces: orphan/approval/lease sweeps that preserve current summary behavior and close an open attempt only when the winning guarded transition corresponds to one.
- Consumes: Task 2 `close_open`; no new attempt is created by a sweeper.

- [ ] **Step 1: Write failing sweeper tests**

  Assert expired leased attempts close as failed with `lease_expired`; a stale lease scan leaves summary/history unchanged. An orphaned unclaimed resume fails its summary with no attempt. Approval expiry after pause leaves the paused attempt unchanged. Legacy running rows without history may fail without fabricated attempts.

- [ ] **Step 2: Add race coverage**

  Exercise stale orphan after claim, stale approval snapshot after re-pause, and stale lease after renewal/change. Only the existing guarded execution winner may close an attempt.

- [ ] **Step 3: Run tests and verify failure**

  Run: `.venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_sweepers.py -q --no-cov`

- [ ] **Step 4: Integrate closure into guarded sweeper transitions**

  Keep scan sessions and per-row update sessions unchanged. When a guarded failure wins, close the matching open attempt if present; no open attempt is a valid legacy/unclaimed case and does not fail the summary transaction. Do not modify notification/event plumbing.

- [ ] **Step 5: Run focused tests**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/integration/db/repos/test_execution_attempt_sweepers.py tests/unit/maintenance/test_execution_sweeper.py -q --no-cov
  .venv\Scripts\python -m pytest tests/integration/sqla_phase4a -q --no-cov
  ```

- [ ] **Step 6: Commit**

  Commit: `feat(execution): preserve attempt history in sweepers`

---

### Task 7: Prove the lifecycle and publish the Phase 5.1 contract

**Files:**
- Create: `tests/integration/execution_attempts/test_lifecycle.py`
- Modify: `docs/superpowers/plans/2026-11-20-telemetry-roadmap.md`
- Modify: `docs/telemetry/signal-catalog.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: verified claim → pause → approval → resume claim → complete history and documents the repository contract consumed by Tasks 5.2/5.3.
- Consumes: Tasks 1–6.

- [ ] **Step 1: Write the end-to-end lifecycle test**

  Execute initial claim → pause → in-app approval → resume claim → completion. Assert two immutable attempts with ordinals `[1, 2]`, results `[paused, completed]`, distinct IDs, exact approval reference on attempt 2, and unchanged attempt 1 fields. Observe the attempt through a separate session after claim to prove commit occurs before graph work.

- [ ] **Step 2: Add history-availability coverage**

  Assert an execution with zero persisted attempts reports `has_history=False`; after its first genuine claim it reports `True`. Empty history is distinct from a missing execution. Do not synthesize rows from `delivery_attempts` or summary timestamps.

- [ ] **Step 3: Run all Phase 5.1 and Phase 4 regression tests**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/integration/db/repos tests/integration/execution_attempts tests/integration/sqla_phase4a tests/integration/sqla_phase4b -q --no-cov
  .venv\Scripts\python -m pytest tests/unit/api/test_internal.py tests/unit/api/test_executions.py tests/unit/api/test_approval_email.py tests/unit/engine/test_langgraph_executor.py tests/unit/maintenance/test_execution_sweeper.py tests/unit/db -q --no-cov
  ```

- [ ] **Step 4: Update documentation**

  Mark Task 5.1 complete only after tests pass. Document that `attempt_id` is created on claim, ordinal starts at 1 for the first real post-migration claim, paused attempts remain immutable after later cancellation/expiry, and recovery transitions remain deferred to Task 7.2.

- [ ] **Step 5: Run full verification**

  Run:

  ```powershell
  .venv\Scripts\python -m ruff check src tests
  .venv\Scripts\python -m ruff format --check src tests
  .venv\Scripts\python -m pyright src tests
  .venv\Scripts\python -m pytest
  ```

- [ ] **Step 6: Commit**

  Commit: `docs(execution): publish durable attempt contract`

## Explicitly deferred

- Task 5.2 role-aware observability API and Task 5.3 UI consume this model in separate reviewed PRs.
- Checkpoint load/save and event-persist spans wait for Claude's SQLAlchemy Phase 5.
- Recovery eligibility, recovery transitions, recovery-source validation, and audit-ledger enforcement remain Tasks 7.1/7.2. This schema reserves nullable references but exposes no recovery control.
- LLM/tool cost aggregation remains Phase 6.

## Self-review record

- **Spec coverage:** attempt identity/ordinal/timing/result/failure/lease/trigger/provenance, atomic lifecycle writes, legacy no-history behavior, pagination/indexes, race tests, and no canceled recovery are assigned to Tasks 1–7.
- **Cross-task interfaces:** Task 1 schema → Task 2 repository → Task 3 claim identity → Task 4 worker fencing → Task 5 approval/cancel provenance → Task 6 sweep closure → Task 7 lifecycle contract.
- **Reserved-path audit:** no task edits `src/storage/**`, event notify/WebSocket modules, checkpointer wiring, or Prisma schema/migrations.
- **Placeholder scan:** no TBD/TODO/“implement later” steps; deliberately deferred roadmap work is listed explicitly above.
- **Type consistency:** `AttemptRecord`, `open_for_claim`, `close_open`, `has_history`, and claim-return fields are defined once and consumed under the same names.
