# Execution Observability API and UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete telemetry Phase 5 by exposing role-safe durable execution observability APIs and rendering lifecycle, attempt, and administrator diagnostics in Composer.

**Architecture:** Add two additive FastAPI reads backed by the existing execution and attempt repositories, with explicit owner and administrator projections. Generate the frontend contract from OpenAPI, then add focused React Query clients and presentation components to the existing history and execution-detail pages. Existing execution behavior, migrations, and recovery transitions remain unchanged.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2 async sessions, pytest, Next.js, React Query, TypeScript, Vitest/Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-26-execution-observability-ui-design.md`

## Global Constraints

- Rebase on the latest `origin/main` before implementation and again before PR review; stop on conflicts in Claude-owned database/checkpointer code.
- The runner may inspect only executions whose `user_id` matches their authenticated user ID; workflow assignment alone does not expose another user's run.
- Missing and unauthorized executions both return `404`.
- Owners never receive thread ID, lease data, delivery counters, raw error text, raw failure codes, actor/reason references, approval IDs, recovery-source IDs, checkpoint data, payloads, or arbitrary event data.
- Administrators receive only the explicitly specified diagnostics fields; repository records are never serialized directly.
- Canceled executions are recovery-ineligible. Failed executions are `assessment_required`; Phase 5 adds no recovery action.
- Existing `GET /executions/{id}`, execution transitions, migrations, WebSocket semantics, and checkpointer behavior remain unchanged.
- All database sessions are short and close before response rendering; no network call occurs while a session is open.
- Attempt pages use ordinal ascending order, default limit 50, maximum 100, and distinguish an existing execution with no history from a missing execution.
- The history page opens no per-row WebSockets or observability queries.
- Do not run `scripts/gcp-bootstrap.ps1`.

---

### Task 1: Role-safe execution observability API

**Files:**
- Create: `src/api/execution_observability.py`
- Modify: `src/main.py`
- Create: `tests/unit/api/test_execution_observability.py`
- Create: `tests/integration/execution_attempts/test_observability_api.py`

**Interfaces:**
- Consumes: `executions_repo.get(session, execution_id)`, `execution_attempts_repo.has_history`, and `execution_attempts_repo.list_page`.
- Produces: `GET /executions/{execution_id}/observability -> ExecutionObservabilityRead`.
- Produces: `GET /executions/{execution_id}/attempts?limit=50&offset=0 -> ExecutionAttemptPage`.
- Produces recovery enum values `not_applicable`, `ineligible_canceled`, and `assessment_required`.
- Produces optional `AdminExecutionDiagnostics` and administrator-only fields on `ExecutionAttemptRead`.

- [x] **Step 1: Write failing projection and guidance unit tests**

  Define table-driven tests that call pure mapping helpers with owner/admin roles. Assert that an owner projection omits all administrator fields, controlled failure codes map to fixed safe guidance, an unknown failure maps to a generic message, canceled maps to `ineligible_canceled`, failed maps to `assessment_required`, and active/completed states map to `not_applicable`.

- [x] **Step 2: Write failing endpoint authorization and pagination tests**

  Cover exact runner access, administrator access, unrelated member `404`, workflow assignee reading another user's execution `404`, inactive-user rejection, legacy execution with `historyAvailable=false`, missing execution `404`, deterministic ordinal ordering, and query validation for `limit=0`, `limit=101`, and negative offset.

- [x] **Step 3: Run the new tests and confirm they fail for the missing router**

  Run:

  ```powershell
  .venv\Scripts\python -m pytest tests/unit/api/test_execution_observability.py tests/integration/execution_attempts/test_observability_api.py -q --no-cov
  ```

  Expected: collection/import failure because `src.api.execution_observability` does not exist.

- [x] **Step 4: Implement explicit Pydantic response models and safe mappings**

  Implement these stable shapes using camel-case aliases:

  ```python
  class RecoveryAssessmentState(StrEnum):
      NOT_APPLICABLE = "not_applicable"
      INELIGIBLE_CANCELED = "ineligible_canceled"
      ASSESSMENT_REQUIRED = "assessment_required"

  class ExecutionObservabilityRead(BaseModel):
      execution_id: str
      workflow_id: str
      status: str
      current_node_id: str | None
      started_at: datetime
      completed_at: datetime | None
      history_available: bool
      attempt_count: int
      failure_guidance: str | None
      recovery_state: RecoveryAssessmentState
      observed_at: datetime
      admin_diagnostics: AdminExecutionDiagnostics | None = None

  class ExecutionAttemptPage(BaseModel):
      history_available: bool
      total: int
      limit: int
      offset: int
      items: list[ExecutionAttemptRead]
  ```

  Use separate owner/admin mapping helpers and fixed dictionaries for safe guidance. Do not use `model_validate(record)` or `dataclasses.asdict` on repository records.

- [x] **Step 5: Implement the endpoints with one short session each**

  Resolve the execution first, apply the exact owner/admin `404` rule, and obtain history/count within the same session. For the summary, a count-only attempt page with `limit=1, offset=0` is acceptable until a dedicated count helper exists; do not add a new database query outside the existing repository. Register the new router in `src/main.py`.

- [x] **Step 6: Run focused API, auth, and existing execution tests**

  ```powershell
  .venv\Scripts\python -m pytest tests/unit/api/test_execution_observability.py tests/integration/execution_attempts tests/unit/api/test_executions_resume.py tests/unit/api/test_internal.py -q --no-cov
  .venv\Scripts\python -m ruff check src/api/execution_observability.py tests/unit/api/test_execution_observability.py tests/integration/execution_attempts/test_observability_api.py
  .venv\Scripts\python -m ruff format --check src/api/execution_observability.py tests/unit/api/test_execution_observability.py tests/integration/execution_attempts/test_observability_api.py
  ```

  Expected: all pass.

- [x] **Step 7: Commit the API task**

  ```powershell
  git add src/api/execution_observability.py src/main.py tests/unit/api/test_execution_observability.py tests/integration/execution_attempts/test_observability_api.py
  git commit -m "feat(api): expose execution observability views"
  ```

---

### Task 2: Generated contract and typed frontend client

**Files:**
- Modify (generated): `frontend/lib/api/generated/schema.ts`
- Modify: `frontend/lib/api/executions.ts`
- Modify: `frontend/lib/api/executions.test.ts`

**Interfaces:**
- Consumes: Task 1 OpenAPI schemas and paths.
- Produces: exported aliases `ExecutionObservabilityRead`, `ExecutionAttemptPage`, and `ExecutionAttemptRead`.
- Produces: `getExecutionObservability(id: string): Promise<ExecutionObservabilityRead>`.
- Produces: `listExecutionAttempts(id: string, params?: {limit?: number; offset?: number}): Promise<ExecutionAttemptPage>`.
- Extends `listExecutions` params with `status?: string`.

- [x] **Step 1: Write failing API-client serialization tests**

  Add tests proving summary calls `/executions/e-1/observability`, attempt pagination calls `/executions/e-1/attempts?limit=25&offset=50`, absent pagination adds no query string, and history status filtering serializes as `status=failed`.

- [x] **Step 2: Run the client test and confirm missing exports/functions fail**

  ```powershell
  Set-Location frontend
  npm test -- lib/api/executions.test.ts
  ```

  Expected: failure because the observability client functions do not exist.

- [x] **Step 3: Regenerate the schema from Task 1 OpenAPI**

  In a backend terminal run:

  ```powershell
  $env:TELEMETRY_ENABLED='false'
  .venv\Scripts\python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
  ```

  In a second terminal run `Set-Location frontend; npm run generate-client`, then stop the backend. Inspect the generated diff and confirm it contains both new paths and schemas. Never hand-edit `schema.ts`.

- [x] **Step 4: Implement the typed client functions**

  Import the generated component schemas, export the three public type aliases, add the two reads, and pass `status` through `listExecutions`.

- [x] **Step 5: Run frontend client, type, and formatting checks**

  ```powershell
  Set-Location frontend
  npm test -- lib/api/executions.test.ts
  npm run lint
  npm run build
  ```

  Expected: all pass.

- [x] **Step 6: Commit the generated contract and client together**

  ```powershell
  git add frontend/lib/api/generated/schema.ts frontend/lib/api/executions.ts frontend/lib/api/executions.test.ts
  git commit -m "feat(frontend): add execution observability client"
  ```

---

### Task 3: Lifecycle, attempt, and administrator components

**Files:**
- Create: `frontend/components/composer/execution-lifecycle-summary.tsx`
- Create: `frontend/components/composer/execution-lifecycle-summary.test.tsx`
- Create: `frontend/components/composer/execution-attempt-timeline.tsx`
- Create: `frontend/components/composer/execution-attempt-timeline.test.tsx`
- Create: `frontend/components/composer/admin-execution-diagnostics.tsx`
- Create: `frontend/components/composer/admin-execution-diagnostics.test.tsx`

**Interfaces:**
- Consumes: generated `ExecutionObservabilityRead`, `ExecutionAttemptPage`, and `ExecutionAttemptRead` types from Task 2.
- Produces: `ExecutionLifecycleSummary({summary, stale})`.
- Produces: `ExecutionAttemptTimeline({page, isLoading, isError, onRetry})`.
- Produces: `AdminExecutionDiagnostics({diagnostics})`.

- [x] **Step 1: Write failing lifecycle-summary tests**

  Assert active, approval-wait, completed, canceled/ineligible, failed/assessment-required, stale, and unknown-status rendering. Assert no recovery button exists.

- [x] **Step 2: Write failing timeline tests**

  Assert loading, refresh error with retry, legacy `historyAvailable=false`, empty history, ordinal ordering, approval association, multi-attempt resume, safe failure guidance, and unknown result/trigger labels.

- [x] **Step 3: Write failing administrator-diagnostics tests**

  Assert thread, lease, delivery count, failure code, and references render only when the diagnostics object is supplied. Assert secrets/checkpoint/payload fields are not accepted by the component contract.

- [x] **Step 4: Run the component tests and confirm missing modules fail**

  ```powershell
  Set-Location frontend
  npm test -- components/composer/execution-lifecycle-summary.test.tsx components/composer/execution-attempt-timeline.test.tsx components/composer/admin-execution-diagnostics.test.tsx
  ```

- [x] **Step 5: Implement the three presentational components**

  Keep status/result/trigger labels in fixed maps with safe generic fallbacks. Use semantic headings, ordered-list markup for chronology, accessible retry controls, and existing Composer badge/card primitives. Do not fetch data inside these components.

- [x] **Step 6: Run component, accessibility-oriented, lint, and type checks**

  ```powershell
  Set-Location frontend
  npm test -- components/composer/execution-lifecycle-summary.test.tsx components/composer/execution-attempt-timeline.test.tsx components/composer/admin-execution-diagnostics.test.tsx
  npm run lint
  npx tsc --noEmit
  ```

  Expected: all pass.

- [x] **Step 7: Commit the component task**

  ```powershell
  git add frontend/components/composer/execution-lifecycle-summary.tsx frontend/components/composer/execution-lifecycle-summary.test.tsx frontend/components/composer/execution-attempt-timeline.tsx frontend/components/composer/execution-attempt-timeline.test.tsx frontend/components/composer/admin-execution-diagnostics.tsx frontend/components/composer/admin-execution-diagnostics.test.tsx
  git commit -m "feat(frontend): render execution attempt timeline"
  ```

---

### Task 4: Integrate observability into run history and detail pages

**Files:**
- Modify: `frontend/app/runs/history/page.tsx`
- Create: `frontend/app/runs/history/page.test.tsx`
- Modify: `frontend/app/runs/[workflowId]/executions/[executionId]/page.tsx`
- Create: `frontend/app/runs/[workflowId]/executions/[executionId]/page.observability.test.tsx`
- Modify: `frontend/e2e/end-user.spec.ts`
- Modify: `frontend/e2e/admin.spec.ts`

**Interfaces:**
- Consumes: Task 2 client functions and Task 3 components.
- Produces: one server-backed history status filter.
- Produces: detail-page queries `['execution-observability', executionId]` and `['execution-attempts', executionId, limit, offset]`.
- Produces: WebSocket invalidation of execution, observability, and attempts for the one open detail page.

- [x] **Step 1: Write failing run-history tests**

  Mock `listExecutions` and assert status selection changes the query argument, pagination/filter changes reset selection safely, empty/error states remain accessible, and no row triggers an observability fetch or WebSocket.

- [x] **Step 2: Write failing detail-page observability tests**

  Mock the three execution client reads. Assert lifecycle and timeline render, admin diagnostics render only when returned by the API, a `404` uses the existing not-found state, last successful data remains visible with a stale warning after refresh error, and active/waiting executions poll while terminal executions stop.

- [x] **Step 3: Run page tests and confirm the missing integrations fail**

  ```powershell
  Set-Location frontend
  npm test -- app/runs/history/page.test.tsx "app/runs/[workflowId]/executions/[executionId]/page.observability.test.tsx"
  ```

- [x] **Step 4: Add the history status filter**

  Pass the selected controlled status to `listExecutions`; retain one list query and existing deletion behavior. Do not fetch attempt counts per row and do not create row WebSockets.

- [x] **Step 5: Add detail-page observability queries and invalidation**

  Fetch summary and the first attempt page independently. Preserve prior data during refetch. On existing WebSocket events, invalidate all three keys for this execution. Use bounded polling only for active/waiting states; terminal states rely on final invalidation and manual refresh.

- [x] **Step 6: Extend owner/admin Playwright scenarios**

  The owner scenario verifies lifecycle and multi-attempt timeline visibility without administrator diagnostics or a recovery action. The administrator scenario verifies diagnostic fields. API assertions confirm an unrelated member receives `404`; do not treat DOM hiding as authorization proof.

- [x] **Step 7: Run focused frontend and Playwright tests**

  ```powershell
  Set-Location frontend
  npm test -- app/runs/history/page.test.tsx "app/runs/[workflowId]/executions/[executionId]/page.observability.test.tsx" components/composer/execution-lifecycle-summary.test.tsx components/composer/execution-attempt-timeline.test.tsx components/composer/admin-execution-diagnostics.test.tsx
  npx playwright test e2e/end-user.spec.ts e2e/admin.spec.ts
  npm run lint
  npm run build
  ```

  Expected: all available local checks pass. If the full E2E stack is unavailable, record the exact prerequisite and leave the scenarios for CI; do not silently skip them.

- [x] **Step 8: Commit the page integration**

  ```powershell
  git add frontend/app/runs/history/page.tsx frontend/app/runs/history/page.test.tsx "frontend/app/runs/[workflowId]/executions/[executionId]/page.tsx" "frontend/app/runs/[workflowId]/executions/[executionId]/page.observability.test.tsx" frontend/e2e/end-user.spec.ts frontend/e2e/admin.spec.ts
  git commit -m "feat(frontend): show execution observability"
  ```

---

### Task 5: Phase 5 completion, documentation, and full verification

**Files:**
- Modify: `docs/superpowers/plans/2026-11-20-telemetry-roadmap.md`
- Modify: `docs/telemetry/signal-catalog.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: completed Tasks 1-4 and their test evidence.
- Produces: Phase 5.2/5.3 completion record and operating guidance.

- [x] **Step 1: Rebase onto current main before final verification**

  ```powershell
  git fetch origin
  git rebase origin/main
  ```

  Resolve only Phase 5-owned conflicts. Stop and report any conflict in database/checkpointer work owned by Claude.

- [x] **Step 2: Update documentation accurately**

  Mark Tasks 5.2 and 5.3 complete only after their checks pass. Document owner/admin field boundaries, legacy empty history, no Phase 5 recovery action, cancellation permanence, query/polling behavior, and the two new endpoints. Preserve all concurrent main documentation entries.

- [ ] **Step 3: Run backend verification against disposable PostgreSQL** _(not ticked: the final backend run is pending; see Outcome)_

  ```powershell
  .venv\Scripts\python -m alembic heads
  .venv\Scripts\python -m pytest tests/unit/api/test_execution_observability.py tests/integration/execution_attempts tests/integration/db/repos tests/integration/sqla_phase4a tests/integration/sqla_phase4b -q --no-cov
  .venv\Scripts\python -m ruff check src tests
  .venv\Scripts\python -m ruff format --check src tests
  .venv\Scripts\python -m pyright src tests
  ```

  Expected: one Alembic head, `0005_execution_attempts`, and all checks pass.

- [ ] **Step 4: Run frontend verification** _(not ticked: the final frontend run is pending; see Outcome)_

  ```powershell
  Set-Location frontend
  npm test
  npm run lint
  npm run build
  ```

  Run the two Playwright files when the documented E2E services are available.

- [x] **Step 5: Run privacy and diff checks**

  Search the new owner models and UI for forbidden fields and verify the generated schema came from OpenAPI. Run `git diff --check origin/main...HEAD` and inspect every changed file for scope.

- [x] **Step 6: Commit Phase 5 completion documentation**

  ```powershell
  git add docs/superpowers/plans/2026-11-20-telemetry-roadmap.md docs/telemetry/signal-catalog.md CHANGELOG.md
  git commit -m "docs(telemetry): complete execution observability phase"
  ```

- [ ] **Step 7: Request task and whole-branch reviews** _(not ticked: the review gate ran and its findings are fixed, see Outcome; the final whole-branch review against the latest main is pending)_

  Review every task diff against this plan, fix all Critical/Important findings, then run one final whole-branch review against the latest `origin/main`. Do not push or merge until review and CI-readiness checks are clean.

## Outcome (2026-11-27)

**Status:** Tasks 1 to 4 implemented; Task 5 documentation done; final verification and the PR are pending (last section).

Codex built this work on a pre-phase-6 base. Claude took over on 2026-11-27 and rebased the branch onto main after SQLAlchemy phase 6 (Prisma removal). The migration is therefore `0005_execution_attempts`, chained after `0004_langgraph_saver_tables`. The first draft of it was numbered `0004`, and any older reference in commit messages or notes means the same revision.

### What shipped

- `GET /executions/{execution_id}/observability` and `GET /executions/{execution_id}/attempts` in `src/api/execution_observability.py`. Access is the execution's runner or an administrator. A workflow assignee does not get another user's run, and a missing or unauthorized execution is `404`. Attempts are ordered by ordinal, default `limit=50`, maximum `100`. An existing execution with no history returns `200` with `historyAvailable=false`.
- Owner projection: lifecycle status, current node, timestamps, `historyAvailable`, `attemptCount`, safe `failureGuidance` (a fixed message per controlled failure code, generic otherwise), `recoveryState` and `observedAt`. Per attempt: ordinal, timing, result, trigger kind, guidance and `hasApproval` (a boolean). Administrators additionally get `adminDiagnostics` (thread ID, failure code, delivery attempts, lease owner and expiry) and, per attempt, the lease, failure code, recovery-source attempt ID, approval ID, actor user ID and reason reference. Event and audit references are omitted rather than invented (no audit ledger exists before Phase 7).
- `recoveryState` is `ineligible_canceled` for canceled, `assessment_required` for failed and `not_applicable` otherwise. There is no recovery action anywhere in the UI.
- Frontend: generated OpenAPI schema and typed client; `ExecutionLifecycleSummary`, `ExecutionAttemptTimeline` and `AdminExecutionDiagnostics`; a server-backed status filter on the run-history page; the detail page's three queries refreshed by its single progress socket, with polling as a fallback. The history page opens no per-row socket or observability query.

### The bug the rebase found

After the rebase onto phase 6 main, the phase 5 oracle tests (fails-closed, sync-failure and no-checkpoint resume) failed. Main's resume path fails a run with `checkpoint_missing` or `checkpoint_sync_failed` through `_mark_failed`, but it did not pass the attempt ID that this branch introduced. The fenced transition refuses an unowned write while an attempt is open, so the run stayed `running` and its attempt never closed. `bcd0a2c` passes `attempt_id` like the other failure handlers. `62ef8ad` adds the regression test that asserts both codes carry it.

### Review-gate findings and their fixes

| Commit | Finding | Fix |
|---|---|---|
| `62ef8ad` | The `bcd0a2c` fix had no regression test. | Unit test pinning `attempt_id` on both resume checkpoint-failure transitions. |
| `806ee91` | Rows paused before the `_pending_approval_since` stamp existed were resumable; the attempts work turned the missing stamp into a 500. | Fall back to the status-only guard (`expected_pending=None`) for those rows, with a test. |
| `576a949` | The 0005 downgrade silently dropped `execution_attempts` and the pending-approval column, destroying the only history. Deleting an approval would scan `workflow_executions`. | Downgrade refuses while either holds data, states the counts and the export-then-`TRUNCATE` path (as 0004 does for checkpoints). The FK is added `NOT VALID` and then validated, and a partial index covers the column. 0005 had not reached production, so it was edited in place. The locking rationale in that commit was corrected afterwards: see the migration docstring. |
| `c849fe0` | A resume-kind claim without `_resume_decision` answered 422 but left the row leased and running, with its attempt open, until the lease sweeper fired. | Fail the row at once, fenced to its attempt (`execution_error`), then return the same 422. A failed write is logged and never masks the 422. Closes the 4a "D9 (kept)" pin and the 4b follow-up. |
| `6c3c6ce` | The 0004 image cancels and lease-fails executions without knowing about attempt rows, so rolling back and forward leaves attempts open and the UI shows "In progress" forever. | `/internal/sweep` gains an isolated reconciliation step that closes open attempts under terminal executions with one guarded `UPDATE` (`failure_code = closed_by_reconciliation`). Runbook section added to `docs/operations/production-deployment.md`. |
| `471a90d` | Every socket event invalidated three queries, so a reconnect replay fired a burst of refetches. The timeline silently showed only the first page of attempts. | Refresh only on `workflow_*` and `approval_*` events behind a 300 ms trailing debounce. The timeline says "Showing N of M attempts" when truncated. |

Smaller rebase and CI fixes: `ab65392` (0004 schema tests pinned to revision 0004 now that 0005 exists), `92a83e7` (pyright strict), `b3d915c` (frontend type-check and prettier), `aea7b61` (leftover Prisma wording in comments).

### Follow-ups skipped on purpose

- **Concurrent blocked-update race tests.** The sweeper, cancel and resume races have deterministic sequential and gathered tests, but no test holds a row lock from a second connection to prove that a blocked guarded `UPDATE` re-evaluates its predicate. The same gap applies to the new reconciliation `UPDATE` (`close_terminal_open_attempts`): its guard is one statement, tested for idempotency and for leaving running or waiting executions alone, not under a real concurrent writer.
- **`has_history` plus count.** `GET /executions/{id}/observability` calls `has_history` and then `list_page(limit=1)` for the count: two queries where one `count` would answer both. Left as is. Both are indexed and cheap, and merging them changes the repository contract.
- **Cosmetic `approval_id` gap during the deploy window.** An attempt opened while an old image and a new image serve traffic side by side can lack its approval reference, so the UI shows `hasApproval=false` for it. It affects display only, never a transition, and ends once the old revision drains.
- **Approvals / `workflow_executions` lock order.** The new foreign key (`ON DELETE SET NULL` from `approvals` to `workflow_executions.pending_attempt_approval_id`) means that deleting an approval and updating its execution can take row locks in opposite orders, so a deadlock is possible in principle. The same lock-order shape pre-dates this branch, and nothing here makes it likely. Noted during migration review and not fixed.

### Verification recorded by Claude when finishing the docs

Run by Claude in the worktree during the documentation step (not the full suite): `scripts/check_doc_links.py`, the unit tests that pin the docs and the signal catalog, `ruff check` and `ruff format --check` on the migration file, `pytest tests/unit` with `LANGSMITH_TRACING=false`, and `git diff --check origin/main...HEAD`. Results are in the hand-off report, not repeated here. The Playwright files were not run by Claude.

### Pending

- Final full-run counts and PR number: **pending** (to be filled in by Balaji after the final full suite run and once the PR is open).
- Task 5 Steps 3, 4 and 7 stay unticked until then.
