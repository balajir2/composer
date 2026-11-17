# Execution Correctness: Cancellation, Deadlines, and Durable Threads

**Date:** 2026-11-17

**Status:** Approved in discussion; awaiting review of written specification

**Scope:** P0-2, P0-3, and P0-5 from `docs/claude-system-audit-fix-list-2026-11-17.md`

## 1. Purpose

Make execution state transitions trustworthy before Composer adds human-assisted recovery, comprehensive audit history, and application telemetry.

This design fixes three related defects:

1. A canceled execution can currently be overwritten by a later executor transition.
2. Sweepers use execution creation time and unconditional updates, so they can fail healthy or already-finished executions.
3. Cloud Tasks, the executor lease, and the assumed maximum execution duration use contradictory deadlines.

The design intentionally separates a durable workflow thread from an active processing attempt. A durable thread and its checkpoints may remain in Postgres for months. By default, a single worker request processes that thread for no more than 1,740 seconds (29 minutes).

## 2. Decisions

### 2.1 Active-processing limit

Composer will initially cap one active processing attempt below Cloud Tasks' 30-minute HTTP-target maximum. It will not implement checkpoint-and-continue across multiple task deliveries in this batch.

One authoritative setting, `EXECUTION_MAX_RUNTIME_SECONDS`, controls the active-processing limit. Its production default is 1,740 seconds (29 minutes). `EXECUTION_SHUTDOWN_GRACE_SECONDS` defaults to 30 seconds and is bounded to the range 1–60 seconds. `ORPHANED_RUNNING_GRACE_SECONDS` defaults to 120 seconds.

Derived timings are:

- executor timeout: `EXECUTION_MAX_RUNTIME_SECONDS`;
- Cloud Tasks dispatch deadline: executor timeout plus `EXECUTION_SHUTDOWN_GRACE_SECONDS`, capped at 1,800 seconds;
- execution lease: the Cloud Tasks dispatch deadline;
- orphaned-running grace period: a separate short setting used only for `running` rows that have no lease.

Configuration validation must reject values that make the executor timeout greater than or equal to the dispatch deadline or make the dispatch deadline exceed 1,800 seconds.

### 2.2 Durable state

The active-processing limit does not impose a retention limit. Composer preserves the execution row, workflow snapshot, checkpoints, events, and future audit records according to explicit retention policy.

The age of a thread is never evidence that its current processing attempt is stale. A months-old thread may resume safely with a new `runningSince` value and a fresh lease.

### 2.3 Cancellation semantics

User cancellation means cancellation, not suspension.

- `canceled` is terminal for the original execution.
- Ordinary resume APIs cannot move a canceled execution back to `running`.
- Cancellation stops further processing after the currently running operation reaches a cooperative boundary.
- Cancellation does not delete the durable thread or its checkpoints.
- A future administrator recovery operation may create a new linked attempt from retained state, but it must not mutate the canceled source execution back to running.

The API and UI must describe cancellation as stopping after the current operation. Composer cannot guarantee recall of an external side effect already accepted by a provider.

### 2.4 Suspension and approval

`suspended` is reserved for a future explicitly resumable pause. This batch does not add the state or its API.

`waiting_approval` remains a durable pause. The existing deployment-wide approval timeout becomes opt-in: zero disables expiry, and production defaults to disabled. A later approval-feature design may add a per-workflow or per-node expiry policy. When an operator explicitly enables the deployment-wide timeout, its sweeper continues to use the approval's pause timestamp rather than thread age.

### 2.5 Expired leases

For this batch, an expired active-processing lease becomes a durable `failed` execution with failure code `lease_expired`. The sweeper does not automatically re-enqueue it.

Automatic continuation requires verified checkpoint semantics, side-effect idempotency, linked attempts, and audit records. Those belong to the later human-assisted recovery design.

## 3. Execution state machine

The relevant transitions are:

```text
queued -> running -> completed
                  -> waiting_approval
                  -> failed
                  -> canceled

waiting_approval -> running
                 -> canceled
                 -> failed (only under an explicit expiry policy)
```

Only an execution still in `running` may transition to `completed`, `waiting_approval`, or executor-originated `failed`.

Every terminal or pause transition uses a conditional database update. A successful transition is defined as exactly one affected row. If no row is affected, the caller lost a race and must not emit the event or side effect associated with that transition.

Examples:

- `_mark_completed` updates only where `id` matches and `status == "running"`.
- `_mark_failed` updates only where `id` matches and `status == "running"`.
- `_mark_waiting_approval` updates only where `id` matches and `status == "running"`.
- The approval email is sent only after `_mark_waiting_approval` wins its transition.
- Completion and failure events are emitted only after their corresponding transition succeeds.

Cancellation continues to use an atomic conditional transition from `running` or `waiting_approval` to `canceled`.

## 4. Cooperative cancellation

The per-node execution wrapper is the cancellation boundary.

Before a node begins, the wrapper reads the execution row. If its status is not `running`, the wrapper raises a dedicated internal stop signal. The executor treats this signal as controlled termination and does not record a failure.

After a node returns, the wrapper checks the status again before emitting node-completion events or permitting the graph to advance. This closes the common race in which cancellation arrives while a node is running.

The guarantees are:

- no later node starts after cancellation is observed;
- the execution's final state remains `canceled`;
- no completion, failure, approval, or approval-email action is emitted after a competing cancellation wins;
- an external operation already in progress may complete;
- cooperative async provider calls may be canceled where the client library supports it, but this is best effort rather than part of the API guarantee.

The internal stop signal must not be caught by a broad exception handler and converted to `failed`.

## 5. Timeouts and task dispatch

Cloud Tasks receives an explicit dispatch deadline for every execution task. The task deadline must be slightly longer than the executor timeout so the executor has an opportunity to persist a stable failure before Cloud Tasks stops waiting.

`LangGraphExecutor` wraps graph invocation in Python 3.11's `asyncio.timeout`. When the timeout expires:

1. graph processing is canceled cooperatively;
2. the executor conditionally transitions the still-running execution to `failed`;
3. it stores failure code `execution_timeout` and a bounded human-readable error;
4. it emits a failure event only if the transition succeeds;
5. it clears lease ownership as part of the successful terminal transition.

If cancellation or another terminal transition won first, the timeout handler performs no state or event write.

## 6. Schema and timestamps

Add `runningSince DateTime?` to `WorkflowExecution`.

Timestamp meanings are:

- `createdAt`: durable thread creation time;
- `runningSince`: start of the current active processing attempt;
- `leaseExpiresAt`: expiry of current worker ownership;
- `completedAt`: entry into any terminal state, following existing project conventions;
- retention timestamps or policy: future concern, separate from processing state.

Set `runningSince` whenever a claim or approval resume successfully changes the execution to `running`. Clear it when leaving `running` for a terminal or paused state.

Add a nullable machine-readable `failureCode String?` column to `WorkflowExecution`. Initial codes are:

- `execution_timeout`;
- `lease_expired`;
- `orphaned_running`;
- `execution_error` for ordinary executor failures.

API additions must be backward compatible: existing `error` text remains available.

The schema migration must be deployed before code that writes the new columns receives traffic, consistent with audit item P1-9.

## 7. Sweeper responsibilities

### 7.1 Lease-expiry sweeper

Select executions where:

- status is `running`;
- `leaseExpiresAt` is non-null;
- `leaseExpiresAt` is earlier than the sweep time.

The failure update repeats all three predicates and requires `leaseExpiresAt` to equal the value read during selection, preventing an old sweep from invalidating a renewed lease. It sets `failed`, `failureCode = "lease_expired"`, a bounded error, `completedAt`, and clears lease/running fields.

It does not enqueue another task in this batch.

### 7.2 Orphaned-running sweeper

Select executions where:

- status is `running`;
- `leaseExpiresAt` is null;
- `runningSince` is earlier than the configured claim-grace cutoff.

This catches a crash or inconsistent transition around worker claim. Its conditional update repeats the selection predicates and uses failure code `orphaned_running`.

Rows with a valid or future lease are never handled by this sweeper, regardless of thread age.

### 7.3 Approval expiration

Approval expiry remains logically separate from stuck-processing detection. `waiting_approval` rows are never considered stuck because of `createdAt`, `runningSince`, or the active-processing timeout.

### 7.4 Retention cleanup

Retention is not a stuck-execution concern. No correctness sweeper deletes checkpoints or durable threads. Any cleanup job must use a separately approved retention policy and must not delete resumable state.

## 8. API and compatibility

- `POST /executions/{id}/cancel` retains its terminal meaning.
- Canceling an already terminal execution continues to return a conflict response.
- Ordinary resume accepts only the expected approval-paused state and never accepts `canceled`.
- Existing execution responses retain their status and error fields.
- Stable failure codes are added without removing human-readable errors.
- No new recovery, suspension, or restart endpoint is introduced in this batch.

## 9. Observability hooks

This batch does not implement the complete telemetry program, but it must leave clear hooks for it:

- structured log fields for execution id, workflow id, transition source, previous status, intended status, and transition outcome;
- explicit log/event distinction between user cancellation, execution timeout, lease expiry, and orphaned running state;
- no secrets, workflow payloads, authorization headers, or decrypted node credentials in these fields;
- counters and traces may be added later without changing lifecycle semantics.

## 10. Testing strategy

Use Postgres-backed integration tests for database races and Prisma predicate behavior. Mocked unit tests may supplement but not replace them.

Required tests:

1. Cancel between two nodes; the second node does not start.
2. Cancel while a side-effecting node is blocked; that node may finish, but the following node does not start and the execution remains canceled.
3. Cancel concurrently with completion; exactly one transition wins and emitted events match the winner.
4. Cancel concurrently with failure; canceled cannot be overwritten.
5. Cancel concurrently with approval pause; an approval email is sent only if `waiting_approval` wins.
6. A months-old thread resumed with a new `runningSince` and valid lease is not swept.
7. A running execution with a future lease is not swept regardless of age.
8. A row completed or canceled between sweeper selection and update is not overwritten.
9. An expired lease becomes `failed` once with `failureCode = "lease_expired"` and is not automatically re-enqueued.
10. A lease-less row inside the claim-grace window is not swept.
11. A lease-less row beyond the claim-grace window fails with `orphaned_running`.
12. Executor timeout records `execution_timeout`, clears the lease, and emits one failure event.
13. Cancellation winning against timeout preserves `canceled` and emits no timeout failure event.
14. Enqueued Cloud Tasks carry the derived explicit dispatch deadline.
15. Startup validation rejects out-of-range or contradictory timing configuration.
16. A canceled execution cannot be resumed through the ordinary resume API.
17. Cancellation does not delete the execution's checkpoint rows.

Run the complete backend quality gates after targeted tests:

```text
ruff check
ruff format --check
pyright
pytest
```

Run the frontend gates if user-visible status wording or behavior changes.

## 11. Documentation and rollout

Update in the same change set:

- `CHANGELOG.md`;
- `docs/decisions.md` with the active-processing-versus-durable-thread decision;
- API documentation for cancellation and failure codes;
- deployment documentation for the authoritative runtime setting;
- GCP bootstrap configuration to remove the contradictory 900-second stuck threshold.

Rollout order:

1. Apply the Prisma migration.
2. Deploy configuration validation and derived timing settings.
3. Deploy guarded transitions, cooperative cancellation, executor timeout, and revised sweepers together so mixed semantics do not persist across revisions.
4. Monitor transition-lost and sweeper logs before beginning the recovery/audit implementation.

No infrastructure is provisioned by this design. Cloud Tasks configuration is changed only through task creation and existing deployment settings.

## 12. Out of scope

- executions longer than 30 minutes through checkpoint-and-continue;
- automatic retry or redelivery after an expired lease;
- side-effect idempotency keys;
- human-assisted recovery UI or API;
- a new `suspended` state;
- linked execution-attempt and immutable audit-event schemas;
- full OpenTelemetry integration;
- workflow versioning beyond any snapshot work independently required by P1-4;
- retention-policy implementation.

These are intentionally deferred until the lifecycle rules in this design are implemented and verified.
