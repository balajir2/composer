# Execution Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cancellation terminal, align every active-processing deadline below Cloud Tasks' 30-minute limit, and prevent sweepers or executor races from corrupting durable execution state.

**Architecture:** A durable workflow thread is independent from a bounded processing attempt. One validated runtime setting derives the executor timeout, Cloud Tasks dispatch deadline, and lease duration; status changes use compare-and-set updates, node boundaries observe cancellation, and sweepers operate on `runningSince` plus lease state rather than thread age.

**Tech Stack:** Python 3.11, FastAPI, Prisma Python, Postgres, LangGraph, Google Cloud Tasks v2, Pydantic Settings v2, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-17-execution-correctness-design.md`

## Global Constraints

- `EXECUTION_MAX_RUNTIME_SECONDS` defaults to 1,740 seconds.
- `EXECUTION_SHUTDOWN_GRACE_SECONDS` defaults to 30 seconds and must be between 1 and 60 seconds.
- Cloud Tasks `dispatch_deadline` equals runtime plus shutdown grace and must not exceed 1,800 seconds.
- `execution_lease_seconds` is derived from the dispatch deadline; it is no longer independently configurable.
- `ORPHANED_RUNNING_GRACE_SECONDS` defaults to 120 seconds.
- User cancellation is terminal; ordinary resume never accepts `canceled`.
- Durable threads and checkpoints are not deleted by cancellation, timeout, or correctness sweepers.
- Expired leases fail durably and are not automatically re-enqueued in this release.
- The deployment-wide approval timeout defaults to disabled (`0`); a positive value remains an explicit operator policy.
- Every state transition that can race uses a conditional update and emits follow-on events or email only after winning.
- Do not add infrastructure, a `suspended` state, recovery endpoints, automatic continuation, or full telemetry in this plan.
- Every behavior change requires tests first, then `CHANGELOG.md` and `docs/decisions.md` updates before the final commit.

## File map

- `src/config.py`: declares processing, grace, orphan, and approval-expiry settings plus derived deadline/lease properties.
- `src/config_validation.py`: rejects contradictory timing values at startup.
- `prisma/schema.prisma` and a new migration: add `runningSince` and `failureCode`.
- `src/api/internal.py`: stamps attempt timestamps during claim and uses structured failure codes on handler crashes.
- `src/api/executions.py`: stamps `runningSince` on approval resume and preserves terminal cancellation semantics.
- `src/engine/langgraph_executor.py`: owns guarded executor transitions and the active-attempt timeout.
- `src/engine/events_wrapper.py`: checks execution status before and after each node.
- `src/engine/execution_control.py` (new): contains the typed internal stop signal and reusable status check, avoiding circular imports and keeping cancellation logic out of telemetry code.
- `src/execution/cloud_tasks.py`: sets the explicit task dispatch deadline.
- `src/maintenance/execution_sweeper.py`: separates expired-lease and lease-less orphan handling, with no automatic replay.
- Existing unit tests under `tests/unit/{engine,execution,maintenance,api}`: fast behavior and race-contract coverage.
- `tests/integration/test_durable_execution.py`: real-Postgres compare-and-set, old-thread, and checkpoint-retention coverage.

---

### Task 1: Authoritative processing deadline configuration

**Files:**
- Modify: `src/config.py:315-366`
- Modify: `src/config_validation.py:32-74`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_config_validation.py`
- Modify: `tests/unit/execution/test_cloud_tasks.py`
- Modify: `src/execution/cloud_tasks.py:142-182`

**Interfaces:**
- Produces: `Settings.execution_max_runtime_seconds: int`
- Produces: `Settings.execution_shutdown_grace_seconds: int`
- Produces: `Settings.orphaned_running_grace_seconds: int`
- Produces: `Settings.execution_dispatch_deadline_seconds: int` property
- Produces: `Settings.execution_lease_seconds: int` property
- Produces: `Settings.approval_wait_timeout_hours: int` defaulting to `0`
- Consumes: Google Cloud Tasks `Task.dispatch_deadline` as `datetime.timedelta`

- [ ] **Step 1: Write failing configuration tests**

Add tests that instantiate `Settings` with a clean environment and assert:

```python
settings = Settings()
assert settings.execution_max_runtime_seconds == 1740
assert settings.execution_shutdown_grace_seconds == 30
assert settings.execution_dispatch_deadline_seconds == 1770
assert settings.execution_lease_seconds == 1770
assert settings.orphaned_running_grace_seconds == 120
```

Add parameterized validation cases for runtime `<= 0`, grace outside `1..60`, and runtime plus grace above `1800`. Each must raise `ProductionConfigError` with the offending setting named in the message.

Change the existing approval-timeout default assertion from `168` to `0` and add a case proving a positive operator-supplied value remains accepted.

- [ ] **Step 2: Run the focused configuration tests and confirm failure**

Run:

```powershell
.venv\Scripts\python -m pytest tests\unit\test_config.py tests\unit\test_config_validation.py -q
```

Expected: failures because the new fields/properties and validation do not exist.

- [ ] **Step 3: Replace independent lease/stuck settings with derived values**

In `Settings`, define the three integer fields and these read-only properties:

```python
@property
def execution_dispatch_deadline_seconds(self) -> int:
    return self.execution_max_runtime_seconds + self.execution_shutdown_grace_seconds

@property
def execution_lease_seconds(self) -> int:
    return self.execution_dispatch_deadline_seconds
```

Remove `execution_stuck_after_seconds` and the mutable `execution_lease_seconds` field. Keep `execution_max_delivery_attempts` temporarily only if another call site still imports it; Task 6 removes recovery use and then removes the setting if `rg` finds no callers.

Change `approval_wait_timeout_hours` to default to `0` and allow all integers `>= 0`; zero means the approval-expiry sweeper is disabled.

Add explicit checks to `validate_production_config`. Use the same validation helper in all environments by extracting `validate_execution_timing(settings)` and calling it during app creation before the production-only checks, so invalid development configuration also refuses startup.

- [ ] **Step 4: Add the explicit Cloud Tasks deadline test**

In `tests/unit/execution/test_cloud_tasks.py`, configure a production-like service account, call `enqueue_execution`, inspect `client.create_task.await_args.kwargs["request"]["task"]`, and assert:

```python
assert task["dispatch_deadline"] == timedelta(seconds=1770)
```

- [ ] **Step 5: Set the task deadline in `enqueue_execution`**

Import `timedelta` and add:

```python
"dispatch_deadline": timedelta(seconds=settings.execution_dispatch_deadline_seconds),
```

at the top level of the Cloud Tasks task object, beside `http_request`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\unit\test_config.py tests\unit\test_config_validation.py tests\unit\execution\test_cloud_tasks.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the deadline contract**

```powershell
git add src/config.py src/config_validation.py src/execution/cloud_tasks.py tests/unit/test_config.py tests/unit/test_config_validation.py tests/unit/execution/test_cloud_tasks.py
git commit -m "fix: unify execution processing deadlines"
```

---

### Task 2: Persist attempt time and structured failure reason

**Files:**
- Modify: `prisma/schema.prisma:51-75`
- Create: `prisma/migrations/20261117000000_execution_attempt_timing/migration.sql`
- Create: `tests/unit/test_prisma_execution_schema.py`

**Interfaces:**
- Produces: `WorkflowExecution.runningSince: datetime | None`
- Produces: `WorkflowExecution.failureCode: str | None`
- Consumes: existing `completedAt`, `leaseOwner`, and `leaseExpiresAt` fields

- [ ] **Step 1: Add a failing schema contract test**

Create `tests/unit/test_prisma_execution_schema.py`, resolve the repository root from `Path(__file__).parents[2]`, read `prisma/schema.prisma` and the new migration, and assert the model contains:

```text
runningSince  DateTime? @map("running_since")
failureCode   String?   @map("failure_code")
```

Also assert the migration contains `ADD COLUMN "running_since" TIMESTAMP(3)` and `ADD COLUMN "failure_code" TEXT`.

- [ ] **Step 2: Run the schema test and confirm failure**

Run the exact new test with `-q`. Expected: failure because both fields are absent.

- [ ] **Step 3: Add the Prisma fields and migration**

Add nullable columns with no backfill. The migration SQL must be:

```sql
ALTER TABLE "workflow_executions"
ADD COLUMN "running_since" TIMESTAMP(3),
ADD COLUMN "failure_code" TEXT;
```

Do not infer `runningSince` from `startedAt`; old active rows must be handled conservatively by rollout rather than assigned a false attempt start.

- [ ] **Step 4: Generate the Prisma client**

Run:

```powershell
uv run prisma generate
```

Expected: exit 0 and generated client recognizes both fields.

- [ ] **Step 5: Run schema and import tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests\unit\test_prisma_execution_schema.py tests\unit\api\test_internal.py -q
```

Expected: pass.

- [ ] **Step 6: Commit the schema change**

```powershell
git add prisma/schema.prisma prisma/migrations/20261117000000_execution_attempt_timing/migration.sql tests/unit/test_prisma_execution_schema.py
git commit -m "feat: persist execution attempt timing"
```

---

### Task 3: Guard executor-owned status transitions

**Files:**
- Modify: `src/engine/langgraph_executor.py:177-246`
- Modify: `tests/unit/engine/test_langgraph_executor.py`

**Interfaces:**
- Produces: `_mark_completed(...) -> bool`
- Produces: `_mark_waiting_approval(...) -> bool`
- Produces: `_mark_failed(execution_id: str, exc: Exception, *, failure_code: str = "execution_error") -> bool`
- Contract: `True` means exactly one row transitioned; only then may callers emit an event or email.

- [ ] **Step 1: Write failing compare-and-set tests**

For each helper, configure `db.workflowexecution.update_many` to return `1` and `0` in separate cases. Assert the `where` includes `{"id": execution_id, "status": "running"}` and the helper returns the matching boolean.

Assert terminal writes clear:

```python
"leaseOwner": None,
"leaseExpiresAt": None,
"runningSince": None,
```

Assert failure writes include the supplied `failureCode`.

- [ ] **Step 2: Run the helper tests and confirm failure**

Run the new test node ids in `tests/unit/engine/test_langgraph_executor.py`. Expected: failure because the implementation uses unconditional `update` and returns `None`.

- [ ] **Step 3: Implement guarded helpers**

Replace each `update` with `update_many`. Return `updated == 1`. Preserve output, variables, and node results only when the guarded completion transition succeeds.

For `waiting_approval`, clear the active lease and `runningSince` because no worker owns a paused execution.

- [ ] **Step 4: Gate all follow-on actions on transition success**

Update both `run` and `resume`:

```python
if await self._mark_waiting_approval(...):
    await send_approval_email(...)
    await self._emit("approval_required", ...)
return
```

Use the same pattern for completion and failure events. If a transition loses, log at `INFO` with execution id and intended status; do not emit or throw.

Use structured `extra` fields named `execution_id`, `transition_source`, `previous_status`, `intended_status`, and `transition_outcome`. Do not include workflow input, node output, error payloads, or credentials.

- [ ] **Step 5: Add race-side-effect tests**

Assert:

- a zero-row approval transition sends no email and emits no approval event;
- a zero-row completion transition emits no completion event;
- a zero-row failure transition emits no failure event;
- successful transitions preserve existing behavior.

- [ ] **Step 6: Run executor tests**

```powershell
.venv\Scripts\python -m pytest tests\unit\engine\test_langgraph_executor.py -q
```

Expected: pass.

- [ ] **Step 7: Commit guarded transitions**

```powershell
git add src/engine/langgraph_executor.py tests/unit/engine/test_langgraph_executor.py
git commit -m "fix: guard executor status transitions"
```

---

### Task 4: Stop graph progression at cancellation boundaries

**Files:**
- Create: `src/engine/execution_control.py`
- Modify: `src/engine/events_wrapper.py:149-260`
- Modify: `tests/unit/engine/test_events_wrapper.py`
- Modify: `tests/unit/engine/test_langgraph_executor.py`

**Interfaces:**
- Produces: `class ExecutionStopped(GraphBubbleUp)` with `execution_id` and `status`
- Produces: `async ensure_execution_running(db: Any, execution_id: str) -> None`
- Consumes: current DB and execution id context variables

- [ ] **Step 1: Write failing status-boundary tests**

Add tests for the wrapper where the row lookup returns `running`, then `canceled` after the executor completes. Assert the wrapped executor runs once, `node_completed` is not emitted, and `ExecutionStopped` propagates.

Add a pre-node case returning `canceled`; assert the executor is never called and `node_started` is not emitted.

- [ ] **Step 2: Run wrapper tests and confirm failure**

```powershell
.venv\Scripts\python -m pytest tests\unit\engine\test_events_wrapper.py -q
```

Expected: new tests fail because there is no status check or stop signal.

- [ ] **Step 3: Add the control module**

Implement:

```python
class ExecutionStopped(GraphBubbleUp):
    def __init__(self, execution_id: str, status: str) -> None:
        self.execution_id = execution_id
        self.status = status
        super().__init__(f"execution {execution_id} stopped with status {status}")


async def ensure_execution_running(db: Any, execution_id: str) -> None:
    row = await db.workflowexecution.find_unique(where={"id": execution_id})
    status = str(row.status) if row is not None else "missing"
    if status != "running":
        raise ExecutionStopped(execution_id, status)
```

Use the normal `find_unique` call already used by the executor; do not introduce a generated selector solely for this check.

- [ ] **Step 4: Reuse current DB from engine context**

Import and call the existing `get_current_db()` paired with `set_current_db()`. Do not pass database clients through workflow state because checkpoint serialization must not contain runtime objects.

- [ ] **Step 5: Check before `node_started` and after executor return**

In `_arun`, when both execution id and DB context exist, call `ensure_execution_running` before any event and again immediately after `executor.arun(state)` returns. Do not catch `ExecutionStopped` in the wrapper's `except Exception` path; inheriting from LangGraph's `GraphBubbleUp` keeps it on the control-flow path.

- [ ] **Step 6: Handle controlled stop in `run` and `resume`**

Add an explicit `except ExecutionStopped` before `except Exception`. Log the observed terminal status and return without calling `_mark_failed` or emitting failure.

Also call `ensure_execution_running` in `run` and `resume` before emitting `workflow_started`; this closes the race where cancellation wins after the Cloud Task claim but before graph invocation begins.

- [ ] **Step 7: Run wrapper and executor tests**

```powershell
.venv\Scripts\python -m pytest tests\unit\engine\test_events_wrapper.py tests\unit\engine\test_langgraph_executor.py -q
```

Expected: pass.

- [ ] **Step 8: Commit cooperative cancellation**

```powershell
git add src/engine/execution_control.py src/engine/events_wrapper.py src/engine/langgraph_executor.py tests/unit/engine
git commit -m "fix: stop execution at cancellation boundaries"
```

---

### Task 5: Bound active graph processing with a durable timeout

**Files:**
- Modify: `src/engine/langgraph_executor.py:248-374`
- Modify: `tests/unit/engine/test_langgraph_executor.py`

**Interfaces:**
- Consumes: `Settings.execution_max_runtime_seconds`
- Produces: timeout failure with `failureCode = "execution_timeout"`

- [ ] **Step 1: Write failing timeout tests for run and resume**

Mock `compiled.ainvoke` with an async function that blocks beyond a tiny configured timeout. Assert `_mark_failed` is awaited with `failure_code="execution_timeout"`, and the successful guarded failure emits exactly one failed workflow event.

Add a race case where `_mark_failed` returns `False`; assert no event is emitted, representing cancellation winning against timeout.

- [ ] **Step 2: Run the timeout tests and confirm failure**

Run the exact new tests. Expected: failure because graph invocation is not wrapped in a timeout.

- [ ] **Step 3: Add one timeout helper used by run and resume**

Avoid duplicating timeout handling. Add:

```python
async def _invoke_with_timeout(self, awaitable: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    async with asyncio.timeout(get_settings().execution_max_runtime_seconds):
        return await awaitable
```

Use it around both fresh `ainvoke(state, ...)` and resume `ainvoke(Command(...), ...)`.

- [ ] **Step 4: Classify timeout separately**

Catch built-in `TimeoutError` before the generic exception block, log without payload data, and call `_mark_failed(..., failure_code="execution_timeout")`. Emit the failed event only when the transition succeeds.

- [ ] **Step 5: Run executor tests**

```powershell
.venv\Scripts\python -m pytest tests\unit\engine\test_langgraph_executor.py -q
```

Expected: pass with no pending-task warnings.

- [ ] **Step 6: Commit the executor timeout**

```powershell
git add src/engine/langgraph_executor.py tests/unit/engine/test_langgraph_executor.py
git commit -m "fix: bound active execution runtime"
```

---

### Task 6: Replace age-based and replaying sweepers

**Files:**
- Modify: `src/maintenance/execution_sweeper.py`
- Modify: `src/api/internal.py` sweep endpoint wiring
- Modify: `src/main.py:60-72`
- Modify: `src/config.py:190-210,315-366`
- Modify: `tests/unit/maintenance/test_execution_sweeper.py`
- Modify: `tests/unit/api/test_internal_sweep.py`
- Modify: `scripts/gcp-bootstrap.ps1:294`

**Interfaces:**
- Produces: `sweep_expired_leases(db, *, now: datetime | None = None) -> SweepResult`
- Produces: `sweep_orphaned_running(db, *, grace_seconds: int, now: datetime | None = None) -> SweepResult`
- Removes: automatic `enqueue_execution` from lease expiry
- Consumes: `Settings.orphaned_running_grace_seconds`

- [ ] **Step 1: Rewrite unit tests to state the new contracts**

Add or change cases so:

- an expired lease is conditionally failed with `failureCode="lease_expired"`;
- no enqueue function is called;
- a renewed lease causes the conditional update to affect zero rows and remains untouched;
- a future lease is not selected;
- an old `createdAt` with a future lease is irrelevant;
- a lease-less row older than `runningSince` cutoff fails with `orphaned_running`;
- a lease-less row with recent `runningSince` is untouched;
- `waiting_approval`, `completed`, and `canceled` never match;
- positive approval timeout runs only when configured; zero skips it.

- [ ] **Step 2: Run sweeper tests and confirm expected failures**

```powershell
.venv\Scripts\python -m pytest tests\unit\maintenance\test_execution_sweeper.py tests\unit\api\test_internal_sweep.py -q
```

Expected: failures reflecting old `startedAt` selection, unconditional updates, and re-enqueue behavior.

- [ ] **Step 3: Make expired lease a compare-and-set failure**

Select `status="running"` and expired non-null lease. For each selected row, use `update_many` with:

```python
where={
    "id": row.id,
    "status": "running",
    "leaseExpiresAt": row.leaseExpiresAt,
}
```

and data setting failed status, `lease_expired`, completion timestamp, and clearing lease/running fields. Remove imports and branches for delivery attempts and re-enqueue.

- [ ] **Step 4: Replace `sweep_stuck_executions` with orphan detection**

Use `runningSince < cutoff` plus `leaseExpiresAt is None`. Repeat those predicates in `update_many`. Do not inspect `startedAt`.

- [ ] **Step 5: Make approval expiry opt-in**

Skip `sweep_expired_approvals` when timeout is zero. Retain positive timeout behavior, but convert its write to `update_many` repeating status and `_pending_approval_since`/cutoff predicate as far as the stored JSON query supports. If Prisma cannot express the JSON time predicate, repeat `id`, `status`, and the exact variables value read so a newer pause cannot be overwritten.

- [ ] **Step 6: Update the internal sweep endpoint and bootstrap**

Call both lease and orphan sweepers from `/internal/sweep`. Update `start_sweeper` and its `src/main.py` caller to accept `orphaned_running_grace_seconds` instead of `stuck_after_seconds`. Remove `EXECUTION_STUCK_AFTER_SECONDS=900` from the GCP environment list; rely on the documented defaults for runtime, shutdown grace, and orphan grace.

- [ ] **Step 7: Run sweeper and internal endpoint tests**

Run the same focused command from Step 2. Expected: pass.

- [ ] **Step 8: Confirm automatic replay is gone**

Run:

```powershell
rg -n "enqueue_execution|max_delivery_attempts|startedAt" src\maintenance\execution_sweeper.py
```

Expected: no enqueue or max-delivery references; `startedAt` appears only in historical comments if any, which should be removed to avoid misleading maintainers.

- [ ] **Step 9: Commit the sweeper model**

```powershell
git add src/maintenance/execution_sweeper.py src/api/internal.py src/main.py tests/unit/maintenance/test_execution_sweeper.py tests/unit/api/test_internal_sweep.py scripts/gcp-bootstrap.ps1 src/config.py
git commit -m "fix: sweep processing attempts by lease state"
```

---

### Task 7: Stamp attempt boundaries during claim, pause, resume, and crash

**Files:**
- Modify: `src/api/internal.py:251-420`
- Modify: `src/api/executions.py:567-684`
- Modify: `tests/unit/api/test_internal.py`
- Modify: `tests/unit/api/test_executions_resume.py`
- Modify: `tests/integration/test_durable_execution.py`

**Interfaces:**
- Consumes: `runningSince`, `failureCode`, and derived `execution_lease_seconds`
- Contract: entering `running` sets one current timestamp; leaving it clears attempt ownership fields.

- [ ] **Step 1: Add failing claim and resume unit tests**

Freeze time and assert claim SQL sets `running_since = $3::timestamptz` while shifting later bind numbers consistently. Assert the lease expiry is `now + settings.execution_lease_seconds`.

Assert approval resume's `update_many` sets:

```python
{
    "status": "running",
    "runningSince": frozen_now,
    "leaseOwner": None,
    "leaseExpiresAt": None,
}
```

The fresh task will establish ownership when claimed.

- [ ] **Step 2: Add failing crash-path assertions**

When `claim_and_run` catches an executor crash, assert its guarded failure sets `failureCode="execution_error"`, `runningSince=None`, and clears lease fields. Preserve the rule that zero affected rows means another terminal transition won.

- [ ] **Step 3: Run focused API tests and confirm failure**

```powershell
.venv\Scripts\python -m pytest tests\unit\api\test_internal.py tests\unit\api\test_executions_resume.py -q
```

Expected: failures for missing attempt fields.

- [ ] **Step 4: Implement attempt timestamp wiring**

Capture `claim_time = datetime.now(UTC)` once. Derive the lease from that same value and write both `running_since` and `lease_expires_at` in the claim transaction. On resume, capture one UTC time and set `runningSince` in the compare-and-set update.

Ensure all failure and pause paths clear ownership consistently.

- [ ] **Step 5: Replace obsolete durable-execution integration expectations**

Change the old test that expects expired leases to re-enqueue. It must now assert:

```python
assert refreshed.status == "failed"
assert refreshed.failureCode == "lease_expired"
assert refreshed.leaseOwner is None
assert refreshed.leaseExpiresAt is None
assert refreshed.runningSince is None
mock_enqueue.assert_not_awaited()
```

Add a months-old execution with `startedAt` in the past, a recent `runningSince`, and a future lease; run both sweepers and assert it remains `running`.

- [ ] **Step 6: Add real-Postgres stale-read protection**

Select an expired row, renew its lease before the sweeper update (using a monkeypatched seam immediately after selection or two explicit DB operations), and assert the old observed lease cannot fail the renewed attempt.

- [ ] **Step 7: Add checkpoint retention after cancellation**

Create an execution and checkpoint row, cancel through the real endpoint, then assert the execution is `canceled` and checkpoint/checkpoint-write rows still exist. Clean them explicitly in `finally`.

- [ ] **Step 8: Run unit tests and available integration tests**

Always run unit tests from Step 3. If `TEST_DATABASE_URL` is set, run:

```powershell
.venv\Scripts\python -m pytest -m integration tests\integration\test_durable_execution.py -q
```

If it is not set, record the integration suite as skipped rather than claiming it passed.

- [ ] **Step 9: Commit attempt-boundary wiring**

```powershell
git add src/api/internal.py src/api/executions.py tests/unit/api/test_internal.py tests/unit/api/test_executions_resume.py tests/integration/test_durable_execution.py
git commit -m "fix: track active execution attempts"
```

---

### Task 8: Complete race coverage, documentation, and release gates

**Files:**
- Modify: `tests/integration/test_durable_execution.py`
- Modify: `docs/api-reference.md`
- Modify: `docs/decisions.md`
- Modify: `docs/operations/production-deployment.md`
- Modify: `docs/operations/gcp-cloud-run-setup.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all lifecycle contracts introduced by Tasks 1–7
- Produces: documented cancellation, timeout, lease, and durability semantics

- [ ] **Step 1: Add real-Postgres terminal race tests**

Use `asyncio.gather` with two separate operations against one execution and assert exactly one compare-and-set transition succeeds for:

- cancel versus completion;
- cancel versus failure;
- cancel versus approval pause.

Assert the final status matches the winner and that loser-associated events/emails are absent. Use separate Prisma transactions or route-plus-executor operations so the test exercises database predicates rather than only mocks.

- [ ] **Step 2: Run the integration race tests**

Run them when `TEST_DATABASE_URL` is available. Expected: pass with one winner per race. If the database is unavailable, do not replace these with mocks; leave the tests marked integration and report the environmental skip.

- [ ] **Step 3: Update API documentation**

Document:

- cancel is terminal and stops after the current operation;
- already-started external side effects may finish;
- durable data remains until retention policy removes it;
- ordinary resume rejects canceled executions;
- `execution_timeout`, `lease_expired`, `orphaned_running`, and `execution_error` failure codes;
- a single active attempt defaults to 29 minutes, while thread lifetime is independent.

- [ ] **Step 4: Record the architecture decision**

Add an ADR entry explaining why Composer chose a bounded processing attempt plus long-lived durable thread instead of one-hour Cloud Tasks requests, immediate checkpoint continuation, or Cloud Run Jobs.

- [ ] **Step 5: Update deployment docs and changelog**

List the three timing settings and their constraints. State that approval expiration is disabled by default and that operators may opt into a deployment-wide timeout. Add migration-before-code rollout order and note the removal of automatic lease replay.

- [ ] **Step 6: Run formatting and static analysis**

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src tests
```

Expected: all commands exit 0.

- [ ] **Step 7: Run the complete backend test suite**

```powershell
uv run pytest
```

Expected: exit 0. Report integration skips separately.

- [ ] **Step 8: Run frontend gates only if user-visible frontend code changed**

If no frontend files changed, record “not applicable.” Otherwise run:

```powershell
Set-Location frontend
npm run format:check
npm run type-check
npm run test
npm run build
Set-Location ..
```

Expected: all four commands exit 0.

- [ ] **Step 9: Verify scope and staged diff**

```powershell
git diff --check
git status --short
rg -n "EXECUTION_STUCK_AFTER_SECONDS|execution_stuck_after_seconds" src scripts docs tests
rg -n "enqueue_execution" src\maintenance\execution_sweeper.py
```

Expected: no whitespace errors, no obsolete stuck setting, no sweeper enqueue call, and no unrelated files staged.

- [ ] **Step 10: Commit documentation and final integration coverage**

```powershell
git add CHANGELOG.md docs tests/integration/test_durable_execution.py
git commit -m "docs: define durable execution lifecycle"
```
