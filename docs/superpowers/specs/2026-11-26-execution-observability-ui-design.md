# Execution Observability API and UI Design

**Date:** 2026-11-26  
**Status:** Approved  
**Roadmap scope:** Telemetry Phase 5, Tasks 5.2 and 5.3  
**Depends on:** Phase 5.1 durable execution-attempt history

## Goal

Give the user who ran a workflow a safe explanation of its durable lifecycle and give administrators the additional operational detail needed to diagnose it. This phase is read-only: it displays recovery assessment state but does not introduce recovery transitions.

## Product rules

- The execution runner may inspect their execution. An assignment to the workflow does not grant access to another user's execution.
- Administrators may inspect all executions.
- Missing and unauthorized executions both return `404` so execution existence is not disclosed.
- User cancellation remains terminal and is always presented as recovery-ineligible.
- Phase 5 does not claim that a failed execution is safely recoverable. Until Phase 7 implements the checkpoint and side-effect assessment, failed executions are classified as `assessment_required` and the UI provides no recovery action.
- The browser never queries the telemetry backend or database directly.
- No response includes checkpoint state, workflow inputs, node inputs or outputs, secrets, raw exception text, or arbitrary event payloads.

## API architecture

Keep the existing `GET /executions/{execution_id}` response unchanged for compatibility. Add a focused router for observability reads.

### Lifecycle summary

`GET /executions/{execution_id}/observability`

The response is role-projected by the server and contains:

- execution identity and current lifecycle status;
- workflow identity;
- current node identity when safe to disclose;
- start and completion timestamps;
- whether durable attempt history exists;
- total attempt count;
- safe failure guidance;
- recovery assessment state: `not_applicable`, `ineligible_canceled`, or `assessment_required`;
- a server timestamp so the UI can identify stale data.

For administrators, the same response additionally contains a nested diagnostics object with thread ID, structured failure code, delivery-attempt count, current lease snapshot, and available event/audit references. Fields that do not exist yet, including audit identifiers, are omitted rather than fabricated.

Owners do not receive thread ID, lease owner or expiry, delivery counters, internal reason references, actor references, raw error text, or raw failure codes. Owner failure guidance is derived from a fixed mapping of controlled failure codes to safe messages; unknown failures use a generic message.

### Attempt timeline

`GET /executions/{execution_id}/attempts?limit=50&offset=0`

The endpoint returns `items`, `total`, `limit`, and `offset`. Results are ordered by attempt ordinal ascending. The maximum page size is 100.

Owner items contain ordinal, start/end timestamps, result, trigger kind, safe failure guidance, and approval association as a boolean rather than an internal approval ID. Opaque attempt IDs may be returned for stable React keys and trace correlation.

Administrator items additionally contain lease owner/expiry, structured failure code, recovery-source attempt ID, approval ID, actor user ID, and reason reference. The endpoint never serializes the repository dataclass directly; owner and administrator models use explicit allowlists.

An existing execution with no attempt rows returns `200` with `items=[]`, `total=0`, and `historyAvailable=false`. A missing or unauthorized execution returns `404`.

### Authorization and sessions

Both endpoints use the existing live-user authentication dependency and the existing execution rule: administrator or exact `execution.user_id`. Each request uses one short SQLAlchemy session and performs no network calls while the session is open.

## Frontend architecture

### History page

The run-history page adds a server-backed status filter using the existing execution-list query parameter. It retains one paginated list query and does not open a WebSocket or observability query for every row. Status and known failure codes receive concise, non-sensitive explanations.

### Execution detail page

The existing execution detail page keeps its current output and node-progress behavior and adds:

1. `ExecutionLifecycleSummary` near the page header;
2. `ExecutionAttemptTimeline` in chronological ordinal order;
3. `AdminExecutionDiagnostics` only when the API supplies the administrator diagnostics object.

The UI distinguishes:

- legacy execution with no durable history;
- loading and refresh failure;
- active and stale data;
- approval wait;
- canceled and permanently ineligible;
- failed and awaiting a future safe-recovery assessment;
- multi-attempt approval/resume history.

There is no recovery button in Phase 5.

### Refresh behavior

The detail page uses separate React Query keys for execution state, observability summary, and attempt pages. The existing per-detail-page WebSocket invalidates the summary and timeline when execution events arrive. Polling remains a bounded fallback while the execution is active or waiting for approval. Terminal executions stop automatic polling after the final invalidation.

The history page uses a modest page-level refetch policy; it never creates one stream per row.

## Error handling

- `404` renders the existing not-found/unauthorized state without distinguishing the cause.
- A summary or timeline refresh failure leaves the last successful data visible with a stale warning and retry control.
- Unknown statuses, trigger kinds, results, and failure codes render safe generic labels.
- Empty history is an intentional state, not an error.

## Testing

Backend tests cover:

- runner access;
- administrator access and additional fields;
- unrelated member and assignee-to-another-user execution receiving `404`;
- inactive-user rejection through the authentication dependency;
- owner fields excluding every administrator-only value;
- legacy empty history versus missing execution;
- pagination bounds and deterministic ordering;
- canceled recovery classification;
- failed recovery classification;
- controlled and unknown failure-code guidance.

Frontend unit/component tests cover loading, empty, stale, unauthorized, canceled, failed/manual-review, approval-wait, and multi-attempt states; admin diagnostics must not render when absent. API-client tests cover query serialization and generated types.

Playwright covers one owner lifecycle and one administrator diagnostic scenario without relying on client-side role hiding as the security boundary.

## Delivery and compatibility

The API lands before the UI and remains additive. OpenAPI types are regenerated from the running backend rather than hand-edited. Phase 5.1 migrations and attempt semantics remain unchanged. No deployment bootstrap script is run as part of this phase.

Phase 5 is complete when an owner can understand what happened without seeing internal operational data, an administrator can diagnose attempts and leases, canceled executions are visibly terminal, and no UI control performs recovery.
