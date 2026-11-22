# Composer Telemetry and Execution Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Composer vendor-neutral, privacy-safe operational telemetry, actionable dashboards and alerts, and an owner/admin execution-observability UI without coupling telemetry to the persistence implementation.

**Architecture:** Composer emits OpenTelemetry traces, metrics, and correlated structured logs through an OTLP boundary. Existing durable execution events remain the product-domain event stream; they are not repurposed as a metrics backend. A later Alembic-managed audit ledger records durable business actions, while recovery remains a separately authorized state transition that cannot ship until its audit records and safe-attempt model exist.

**Tech Stack:** FastAPI, OpenTelemetry Python SDK and contrib instrumentation, OTLP, OpenTelemetry Collector, Google Cloud Monitoring/Trace/Logging, LangSmith, PostgreSQL, SQLAlchemy 2 and Alembic after the active migration lands, Next.js/React Query for the operational UI.

**Spec:** [`docs/claude-system-audit-fix-list-2026-11-17.md`](../../claude-system-audit-fix-list-2026-11-17.md), Part B items B-4/B-5/B-6/B-9 and “Cross-cutting design requirements for telemetry, recovery and audit.” Current-state baseline: [`docs/operations/observability.md`](../../operations/observability.md).

## Global Constraints

- Claude's SQLAlchemy/Alembic migration is the persistence baseline. Do not add or modify Prisma models, Prisma migrations, or Prisma-specific telemetry code.
- Phases 0–4 must remain ORM-independent. Database-backed telemetry, audit, or UI read models start only after the Alembic migration is merged and its model/session conventions are stable.
- Application code exports OTLP only. Google Cloud-specific routing belongs in collector/deployment configuration, not business logic.
- Telemetry export is best-effort and must never change execution status, block workflow progress, or expose exporter failures to end users.
- Metrics attributes must be bounded. Never label metrics with `execution_id`, `thread_id`, `attempt_id`, `workflow_id`, `user_id`, email, URL, prompt, model response, node input/output, exception text, or arbitrary provider payloads.
- Trace and log records may carry opaque correlation identifiers (`request_id`, `execution_id`, `thread_id`, `attempt_id`, `node_id`) but never secrets or raw workflow payloads.
- Centralized redaction must remove authorization/cookie headers, provider keys, decrypted node credentials, signed approval tokens, connection strings, and configured sensitive fields before export.
- Existing LangSmith tracing remains optional and LLM-focused. Its runs must correlate to Composer execution and attempt IDs, but LangSmith must not become the only application-observability destination.
- Existing `ExecutionEvent` records remain finite-retention operational/domain events. The future audit ledger is separate, append-only, unsampled, access-controlled, and exportable.
- User cancellation remains terminal. Telemetry or recovery work must not introduce automatic replay or make canceled executions recoverable.
- Recovery is owner-accessible only when the backend classifies the failed attempt as safely recoverable. Ambiguous side-effect outcomes require manual review; administrators may inspect all authorized details.
- Every phase is independently deployable and reversible. Feature flags default new exporters, dashboards, and UI panels off until their rollout gate passes.

---

## Roadmap overview

| Phase | Outcome | Depends on | Exit gate |
|---|---|---|---|
| 0 | Signal contract, privacy policy, and baseline | None | Contract reviewed; no schema/code changes |
| 1 | OTLP foundation and structured correlation | Phase 0 | Export disabled safely; in-memory tests pass |
| 2 | End-to-end execution traces and logs | Phase 1 | One execution is traceable request → node → terminal state |
| 3 | Operational metrics | Phase 2 | Required counters/histograms observable without high-cardinality labels |
| 4 | Collector, dashboards, SLOs, and alerts | Phase 3 | Staging alerts exercised and linked to runbooks |
| 5 | Execution observability UI | Phase 4 + merged Alembic migration | Owners/admins see role-appropriate thread and attempt status |
| 6 | LLM/tool usage and cost | Phase 3 | Provider usage reconciles with sampled executions and budgets |
| 7 | Audit ledger and safe recovery telemetry | Phase 5 + audit schema | Every override is attributable; canceled stays terminal |
| 8 | Enterprise hardening and general availability | Phases 1–7 | Load, privacy, retention, tenancy, and rollback gates pass |

## Canonical signal contract

### Correlation identifiers

| Identifier | Created at | Propagated through | Allowed in |
|---|---|---|---|
| `request_id` | HTTP ingress; accept trusted W3C trace context, create local request ID otherwise | response headers, request logs, enqueue span | traces, logs |
| `workflow_id` | execution creation | execution spans and authorized UI responses | traces, logs; never metrics |
| `execution_id` | execution row creation | Cloud Task body, worker context, events, WebSocket | traces, logs; never metrics |
| `thread_id` | durable execution creation | checkpoint operations and recovery assessment | traces, logs; admin-only UI |
| `attempt_id` | generated as an opaque UUID at every successful claim; persisted once Phase 5 lands | worker, node spans, lease lifecycle | traces, logs, audit; never metrics |
| `node_id` | node execution boundary | node/provider/tool spans | traces, logs; never metrics |

### Metric names and bounded attributes

| Instrument | Type | Unit | Bounded attributes |
|---|---|---|---|
| `composer.execution.started` | Counter | `{execution}` | `trigger_kind`, `environment` |
| `composer.execution.terminal` | Counter | `{execution}` | `status`, `failure_code`, `trigger_kind` |
| `composer.execution.attempt.duration` | Histogram | `s` | `result`, `failure_code` |
| `composer.execution.queue_wait` | Histogram | `s` | `trigger_kind`, `result` |
| `composer.execution.node.duration` | Histogram | `s` | `node_type`, `result` |
| `composer.execution.approval_wait` | Histogram | `s` | `decision`, `expired` |
| `composer.execution.lease_expired` | Counter | `{attempt}` | `failure_code` |
| `composer.execution.orphaned` | Counter | `{attempt}` | `failure_code` |
| `composer.execution.recovery` | Counter | `{attempt}` | `result`, `eligibility_reason` |
| `composer.provider.request.duration` | Histogram | `s` | `provider`, `operation`, `result` |
| `composer.provider.request` | Counter | `{request}` | `provider`, `operation`, `result` |
| `composer.llm.tokens` | Counter | `{token}` | `provider`, `model_family`, `token_type` |
| `composer.llm.cost` | Counter | `USD` | `provider`, `model_family`, `cost_source` |
| `composer.maintenance.sweep` | Counter | `{item}` | `component`, `result` |

`failure_code`, `eligibility_reason`, `node_type`, `provider`, `operation`, and `result` must be enums or repository-controlled vocabularies. Unknown values collapse to `other`; arbitrary exception messages never become attributes.

### Span hierarchy

```text
HTTP request
└── composer.execution.enqueue
    └── composer.execution.claim
        └── composer.execution.attempt
            ├── composer.checkpoint.load
            ├── composer.node.execute
            │   ├── composer.provider.call
            │   └── composer.tool.call
            ├── composer.checkpoint.save
            └── composer.event.persist
```

Cloud Tasks breaks the synchronous parent/child call chain. Inject W3C trace context into task headers when enqueueing and extract it at `/internal/claim-and-run`; also add span links keyed by the persisted execution/attempt correlation when delivery is delayed or retried.

---

## Phase 0 — Contract, privacy, and baseline

### Task 0.1: Freeze the telemetry contract

**Files:**
- Create: `docs/telemetry/signal-catalog.md`
- Create: `docs/telemetry/privacy-and-cardinality.md`
- Modify: `docs/operations/observability.md`
- Test: `tests/unit/telemetry/test_signal_catalog.py`

**Interfaces:**
- Produces: the metric names, span names, controlled vocabularies, attribute allow-list, and redaction rules consumed by every later phase.
- Consumes: current lifecycle vocabulary from `src/api/execution_status.py`, failure codes from execution code, and the contract in this roadmap.

- [ ] Inventory every current execution status, failure code, trigger kind, node type, provider, and maintenance result; encode the allowed values in the signal catalog.
- [ ] Add a test that parses the catalog and fails on duplicate metric names, missing units, unbounded attributes, or forbidden identifier attributes.
- [ ] Document payload classes that are always forbidden, configurable, or allowed; include prompts, responses, uploaded document text, HTTP bodies, headers, tokens, emails, and exception text.
- [ ] Record a baseline from staging for execution count, failure count, p50/p95 duration, lease expiry, and provider failure rate using existing DB/log queries. Store the query definitions, not customer data.
- [ ] Review the contract with operations, security/privacy, and product owners before adding SDK dependencies.
- [ ] Commit as `docs(telemetry): define signal and privacy contracts`.

**Acceptance criteria:** Every future signal has a declared owner, type, unit, bounded attributes, retention class, and redaction classification. No runtime behavior changes.

## Phase 1 — OpenTelemetry foundation

### Task 1.1: Add an isolated telemetry package

**Files:**
- Create: `src/telemetry/__init__.py`
- Create: `src/telemetry/attributes.py`
- Create: `src/telemetry/redaction.py`
- Create: `src/telemetry/runtime.py`
- Modify: `src/config.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Test: `tests/unit/telemetry/test_attributes.py`
- Test: `tests/unit/telemetry/test_redaction.py`
- Test: `tests/unit/telemetry/test_runtime.py`

**Interfaces:**
- Produces: `TelemetryRuntime.start(app_name: str, app_version: str, environment: str) -> None`, `TelemetryRuntime.shutdown() -> None`, `safe_attributes(kind: SignalKind, values: Mapping[str, object]) -> dict[str, AttributeValue]`, and `redact(value: object, *, max_bytes: int) -> object`.
- Consumes: configuration only; no database/session dependency.

- [ ] Write failing tests proving disabled telemetry installs no exporters, startup is idempotent, shutdown flushes with a bounded timeout, and exporter failure never raises into application code.
- [ ] Write table-driven redaction tests containing bearer tokens, cookies, API keys, signed approval URLs, database URLs, nested credentials, long fields, and benign values.
- [ ] Add `opentelemetry-sdk`, OTLP exporter, FastAPI/ASGI instrumentation, HTTPX instrumentation, and logging instrumentation through `uv`; commit the resolved `uv.lock`.
- [ ] Add settings: `telemetry_enabled=false`, `otel_exporter_otlp_endpoint`, `otel_exporter_otlp_headers`, `otel_traces_sampler=parentbased_traceidratio`, `otel_traces_sampler_arg=0.10`, `telemetry_export_timeout_seconds=5`, `telemetry_max_attribute_bytes=1024`, and `telemetry_service_namespace=composer`.
- [ ] Validate production configuration: enabling telemetry requires an HTTPS or explicitly local OTLP endpoint; never log exporter headers.
- [ ] Implement the runtime with batch span/metric processors and bounded queues. Use in-memory exporters in tests.
- [ ] Run `pytest tests/unit/telemetry -v`, Ruff, Pyright, and the production-config tests.
- [ ] Commit as `feat(telemetry): add vendor-neutral OTLP runtime`.

### Task 1.2: Correlate HTTP requests and structured logs

**Files:**
- Create: `src/telemetry/context.py`
- Create: `src/telemetry/logging.py`
- Modify: `src/main.py`
- Modify: `src/security/proxy_headers.py`
- Test: `tests/unit/telemetry/test_context.py`
- Test: `tests/unit/telemetry/test_logging.py`
- Test: `tests/unit/test_main.py`

**Interfaces:**
- Produces: `bind_context(**ids: str | None) -> ContextManager[None]`, `current_correlation() -> CorrelationContext`, and JSON log fields `trace_id`, `span_id`, `request_id`, `execution_id`, `attempt_id`, `node_id`.
- Consumes: `TelemetryRuntime` from Task 1.1.

- [ ] Write failing tests for valid/invalid W3C trace context, generated request IDs, response propagation, context cleanup between requests, and redaction before JSON serialization.
- [ ] Initialize telemetry before router activity and shut it down after application services stop; preserve the existing app lifecycle ordering.
- [ ] Instrument FastAPI while excluding `/health` from sampled traces and ensuring 4xx/5xx status attributes follow semantic conventions.
- [ ] Replace `logging.basicConfig` with one structured formatter that works when telemetry is disabled and adds correlation fields when enabled.
- [ ] Confirm security middleware still runs outermost and untrusted forwarding headers cannot overwrite trace or request identity.
- [ ] Run focused tests, then the full non-live suite.
- [ ] Commit as `feat(telemetry): correlate requests and structured logs`.

**Phase 1 exit gate:** With a test OTLP receiver unavailable, the app starts, executes workflows, and shuts down normally. With an in-memory exporter, request spans and JSON logs carry matching trace/request IDs and contain no seeded secrets.

## Phase 2 — Execution, node, checkpoint, and delivery traces

### Task 2.1: Propagate context through Cloud Tasks

**Files:**
- Create: `src/telemetry/propagation.py`
- Modify: `src/execution/cloud_tasks.py`
- Modify: `src/api/internal.py`
- Test: `tests/unit/telemetry/test_propagation.py`
- Test: `tests/unit/execution/test_cloud_tasks.py`
- Test: `tests/unit/api/test_internal.py`

**Interfaces:**
- Produces: `inject_task_headers(headers: MutableMapping[str, str]) -> None` and `extract_task_context(headers: Mapping[str, str]) -> Context`.
- Consumes: W3C Trace Context plus execution identifiers already present in the task payload/database.

- [ ] Prove by tests that enqueue injects `traceparent`/`tracestate`, worker extraction creates the correct remote parent or link, and malformed headers fall back safely.
- [ ] Add spans for enqueue latency, claim outcome, delivery attempt number, and claim rejection without recording task tokens or request bodies.
- [ ] Preserve Cloud Tasks idempotency and claim semantics; tracing failures must not alter response status or database transitions.
- [ ] Run Cloud Tasks and internal endpoint unit/integration tests.
- [ ] Commit as `feat(telemetry): propagate traces through durable delivery`.

### Task 2.2: Trace execution attempts and nodes

**Files:**
- Create: `src/telemetry/execution.py`
- Modify: `src/engine/langgraph_executor.py`
- Modify: `src/engine/events_wrapper.py`
- Modify: `src/storage/checkpointer.py` or its post-Alembic replacement selected by Claude's migration
- Modify: `src/engine/events_pg.py` or its post-Alembic replacement
- Test: `tests/unit/telemetry/test_execution_tracing.py`
- Test: `tests/unit/engine/test_events_wrapper.py`
- Test: `tests/unit/engine/test_langgraph_executor.py`

**Interfaces:**
- Produces: `execution_attempt_span(context: ExecutionTelemetryContext)`, `node_span(node_id: str, node_type: str)`, and checkpoint/event persistence spans.
- Consumes: immutable `ExecutionTelemetryContext(execution_id, workflow_id, thread_id, attempt_id, trigger_kind)` created at claim time.

- [ ] Generate one opaque UUID when a claim succeeds and use it for that processing attempt's telemetry context. Before Phase 5 this ID is trace/log correlation only; Phase 5 persists the same identifier for durable history. Never derive it from mutable timestamps.
- [ ] Test run, approval resume, timeout, cancel race, failure, and exporter-failure paths. Span status must reflect the winning persisted transition, not a stale local outcome.
- [ ] Wrap node execution without recording node input/output. Record `node.type`, result, duration, and controlled failure code.
- [ ] Trace checkpoint load/save and durable event append; store counts/byte sizes only, not serialized state.
- [ ] Preserve the rule that telemetry emission failure cannot corrupt execution state or suppress terminal events.
- [ ] Run lifecycle race tests and real-Postgres integration tests against the Alembic schema.
- [ ] Commit as `feat(telemetry): trace durable execution attempts`.

**Phase 2 exit gate:** A staging trace follows one run from HTTP creation through task enqueue, claim, attempt, nodes, checkpoints, events, and terminal transition. Approval resume forms a new attempt correlated to the same durable thread. No span contains workflow payloads or secrets.

## Phase 3 — Operational metrics

### Task 3.1: Implement the canonical metric instruments

**Files:**
- Create: `src/telemetry/metrics.py`
- Modify: `src/engine/langgraph_executor.py`
- Modify: `src/engine/events_wrapper.py`
- Modify: `src/api/executions.py`
- Modify: `src/api/approval_email.py`
- Modify: `src/api/internal.py`
- Modify: `src/maintenance/execution_sweeper.py`
- Test: `tests/unit/telemetry/test_metrics.py`
- Test: lifecycle tests already covering the modified transitions

**Interfaces:**
- Produces: typed methods on `ComposerMetrics`: `execution_started`, `execution_terminal`, `attempt_finished`, `queue_wait_observed`, `node_finished`, `approval_wait_observed`, `provider_finished`, `maintenance_result`, and `recovery_result`.
- Consumes: only controlled enums and numeric durations; methods reject unknown attribute keys.

- [ ] Test every canonical instrument's name, type, unit, and exact allowed attributes using an in-memory metric reader.
- [ ] Add a cardinality guard test that fails if IDs, emails, URLs, exception messages, or arbitrary strings are passed as metric attributes.
- [ ] Increment counters only after the corresponding authoritative state transition wins.
- [ ] Measure queue wait from persisted enqueue/create time to claim time; measure attempt duration from attempt start to terminal/pause boundary.
- [ ] Record lease/orphan/sweeper outcomes once per winning conditional update, not once per scanned row.
- [ ] Do not implement distributed queue depth as an in-process gauge. Export count and oldest-age observations from the scheduler-driven sweep/readiness query so values represent the database source of truth.
- [ ] Run telemetry and lifecycle tests, then full CI gates.
- [ ] Commit as `feat(telemetry): emit bounded execution metrics`.

### Task 3.2: Instrument outbound providers, tools, and WebSockets

**Files:**
- Create: `src/telemetry/providers.py`
- Modify: `src/llm/providers.py`
- Modify: `src/mcp/client.py`
- Modify: `src/tools/base.py`
- Modify: `src/api/events_ws.py`
- Test: `tests/unit/telemetry/test_provider_metrics.py`
- Test: existing LLM/MCP/WebSocket tests

**Interfaces:**
- Produces: `provider_call(provider: ProviderName, operation: ProviderOperation)` context manager returning a result recorder.
- Consumes: repository-controlled provider and operation enums; hostnames, paths, query strings, MCP arguments, prompts, and responses are excluded.

- [ ] Test success, timeout, rate-limit, authentication, provider-error, and client-cancel outcomes.
- [ ] Record provider latency and result at the lowest shared call boundary to prevent double-counting nested executors.
- [ ] Add WebSocket connection duration, reconnect, replay count, and send-failure metrics with no client or execution identifiers in labels.
- [ ] Verify LangSmith behavior is unchanged when OpenTelemetry is disabled or enabled.
- [ ] Commit as `feat(telemetry): measure provider and event delivery health`.

**Phase 3 exit gate:** Staging exports every metric in the canonical catalog. A load run demonstrates stable time-series cardinality as executions and users increase.

## Phase 4 — Collection, dashboards, readiness, and alerts

### Task 4.1: Deploy an OTLP collector boundary

**Files:**
- Create: `deploy/otel-collector/config.yaml`
- Modify: `Dockerfile` or Cloud Run multi-container deployment assets selected by the deployment design
- Modify: `scripts/gcp-bootstrap.ps1`
- Modify: `docs/operations/gcp-cloud-run-setup.md`
- Modify: `docs/operations/production-deployment.md`
- Test: `tests/unit/telemetry/test_collector_config.py`

**Interfaces:**
- Produces: OTLP ingestion endpoint for application instances and Google Cloud exporters for trace, metric, and log destinations.
- Consumes: workload identity/service account credentials; no static GCP key files.

- [ ] Validate collector configuration in CI and fail on debug exporters, insecure public receivers, missing memory limits, or unbounded queues in production.
- [ ] Add memory limiter, batch processor, resource enrichment, retry, sending queue, and health extension.
- [ ] Configure application OTLP export to the collector and collector export to Google Cloud; keep local development disabled by default.
- [ ] Grant the runtime only the telemetry writer roles required by the chosen Google Cloud destinations.
- [ ] Exercise collector unavailable/restarting/overloaded scenarios and prove Composer execution remains correct.
- [ ] Commit as `feat(ops): deploy OTLP collector for Composer`.

### Task 4.2: Define SLOs, dashboards, and alerts as code

**Files:**
- Create: `deploy/monitoring/composer-dashboard.json`
- Create: `deploy/monitoring/alerts.yaml`
- Create: `docs/operations/telemetry-slos.md`
- Modify: `docs/operations/monitoring.md`
- Modify: `docs/operations/incident-response.md`
- Test: `tests/unit/telemetry/test_monitoring_config.py`

**Interfaces:**
- Produces: versioned dashboard/alert definitions and runbook links.
- Consumes: Phase 3 metric names only.

- [ ] Define availability and successful-execution SLIs separately; a healthy HTTP service can still have unhealthy workflow processing.
- [ ] Add panels for request rate/error/latency, executions by terminal state, queue wait, attempt duration, node duration, leases/orphans, approval wait, provider latency/errors, WebSocket delivery, and telemetry exporter health.
- [ ] Add initial alerts for oldest queue age, execution failure ratio, timeout/lease/orphan spikes, claim failures, recovery failures, provider error ratio, telemetry export loss, and abnormal token/cost spend.
- [ ] Use minimum-volume guards and multi-window evaluation to avoid paging on one failure in a low-traffic deployment.
- [ ] Attach an owner, severity, evaluation window, and runbook anchor to every alert.
- [ ] Trigger each alert in staging and record evidence in the deployment checklist.
- [ ] Commit as `feat(ops): add Composer telemetry dashboards and alerts`.

### Task 4.3: Add database-aware readiness

**Files:**
- Modify: `src/main.py`
- Create: `src/api/readiness.py`
- Test: `tests/unit/api/test_readiness.py`
- Modify: deployment health-check configuration and operations docs

**Interfaces:**
- Produces: `/health` remains process liveness; `/ready` checks database reachability and critical runtime initialization with strict timeouts.
- Consumes: SQLAlchemy session/engine health API after the Alembic migration, plus telemetry runtime health as a non-blocking diagnostic field.

- [ ] Test ready, database timeout, migration mismatch, draining, and telemetry-exporter-unavailable cases.
- [ ] Return non-200 for database or migration readiness failure; telemetry exporter degradation is reported but does not make the service unready.
- [ ] Ensure responses contain component states but no connection strings or exception details.
- [ ] Point deployment readiness probes at `/ready` while liveness remains `/health`.
- [ ] Commit as `feat(ops): add database-aware readiness probe`.

**Phase 4 exit gate:** Staging dashboards populate from OTLP, every alert has been exercised, runbooks identify first-response queries, and killing the collector does not interrupt workflows.

## Phase 5 — Durable attempt model and execution observability UI

**Phase-start gate:** Re-read this roadmap after Claude's SQLAlchemy/Alembic migration merges and write the phase-specific execution plan using the exact merged model, repository, session, and migration paths. Preserve the interfaces and acceptance criteria below; do not revive Prisma paths from older commits.

### Task 5.1: Add an explicit processing-attempt read model

**Files:**
- Create through Alembic: `execution_attempts` table and migration using the post-migration repository conventions
- Create: SQLAlchemy model/repository modules following Claude's merged layout
- Modify: execution claim, pause, resume, terminal transition, and sweeper services
- Test: unit repository tests and real-Postgres lifecycle integration tests

**Interfaces:**
- Produces: immutable attempt identity, ordinal, start/end timestamps, result, failure code, lease owner/expiry, trigger kind, recovery source, and actor/reason references.
- Consumes: the durable execution/thread identity and conditional-transition rules.

- [ ] Create a new attempt for initial claim, approval resume, and approved recovery; never overwrite prior attempt timing/failure fields.
- [ ] Keep `WorkflowExecution` as the durable thread/current summary and `ExecutionAttempt` as append-oriented processing history.
- [ ] Prove concurrent claim/resume/cancel/recovery can create at most one winning attempt.
- [ ] Backfill no fictional history: legacy executions expose an “attempt history unavailable” marker until a real post-migration attempt exists.
- [ ] Add pagination and indexes for execution/time/status/admin operational queries.
- [ ] Commit as `feat(execution): persist processing attempt history`.

### Task 5.2: Expose role-aware observability APIs

**Files:**
- Modify: `src/api/executions.py`
- Create: `src/api/execution_observability.py`
- Modify: OpenAPI-generated frontend schema
- Test: `tests/unit/api/test_execution_observability.py`

**Interfaces:**
- Produces: owner-safe execution summary/attempt timeline and admin diagnostics including lease data and recovery assessment.
- Consumes: attempt repository and existing authorization dependencies.

- [ ] Owners receive lifecycle state, attempt ordinal/timing/result, safe failure guidance, and recovery eligibility without lease owner, internal error detail, or cross-user data.
- [ ] Admins receive thread ID, leases, failure codes, delivery attempts, event/audit references, and “manual review required” reasons.
- [ ] Paginate attempt/event lists and cap returned payload sizes; never expose raw checkpoint blobs.
- [ ] Test owner, assignee, unrelated member, inactive user, and admin authorization.
- [ ] Commit as `feat(api): expose execution observability views`.

### Task 5.3: Build the execution operations UI

**Files:**
- Modify: `frontend/app/runs/history/page.tsx`
- Modify: `frontend/app/runs/[workflowId]/executions/[executionId]/page.tsx`
- Create: `frontend/components/composer/execution-lifecycle-summary.tsx`
- Create: `frontend/components/composer/execution-attempt-timeline.tsx`
- Create: `frontend/components/composer/admin-execution-diagnostics.tsx`
- Modify: `frontend/lib/api/executions.ts`
- Test: component tests and Playwright owner/admin scenarios

**Interfaces:**
- Produces: owner lifecycle summary and admin diagnostics panel.
- Consumes: Task 5.2 API; no direct telemetry backend queries from the browser.

- [ ] Add status filters, failure-code explanations, attempt timeline, approval wait, cancellation, recovery eligibility, and manual-review guidance.
- [ ] Show leases and internal diagnostics only to admins; authorization remains enforced server-side.
- [ ] Use polling/WebSocket invalidation without opening one high-frequency stream per history row.
- [ ] Test loading, empty, stale, unauthorized, canceled, failed-recoverable, failed-ambiguous, and multi-attempt states.
- [ ] Commit as `feat(frontend): show durable execution attempts`.

**Phase 5 exit gate:** An owner can understand what happened and whether they may recover; an admin can diagnose lease/attempt state; no UI control can recover canceled or ambiguous executions.

## Phase 6 — LLM, tool, usage, and cost telemetry

### Task 6.1: Normalize provider usage

**Files:**
- Create: `src/telemetry/usage.py`
- Modify: `src/llm/providers.py`
- Modify: `src/llm/judgment.py`
- Modify: provider/tool call boundaries
- Test: `tests/unit/telemetry/test_usage.py`

**Interfaces:**
- Produces: `UsageRecord(provider, model_family, input_tokens, output_tokens, cached_tokens, cost_usd, cost_source)` and metric emission.
- Consumes: provider-returned usage only; estimates are explicitly marked and never presented as invoices.

- [ ] Normalize usage responses for every supported LLM provider and structured-output path.
- [ ] Record missing usage as unavailable, not zero.
- [ ] Add model-pricing configuration with effective dates and source attribution; provider-billed cost wins over estimates.
- [ ] Correlate LangSmith runs with Composer trace/execution/attempt IDs without duplicating prompts or outputs into OpenTelemetry.
- [ ] Reconcile a staging sample against provider/LangSmith reports within a documented tolerance.
- [ ] Commit as `feat(telemetry): capture normalized LLM usage`.

### Task 6.2: Add administrator usage and budget views

**Files:**
- Add Alembic-managed usage aggregation tables only if query cost requires them; raw telemetry remains outside Postgres
- Create admin usage API and `frontend/app/admin/usage/page.tsx`
- Test authorization, aggregation, and budget notification behavior

**Interfaces:**
- Produces: daily usage/cost aggregates by deployment, workflow, and user for authorized admins; budget thresholds and notifications.
- Consumes: normalized usage records and centralized-key ownership model.

- [ ] Keep user/workflow attribution out of metric labels; aggregate through the authorized usage pipeline or warehouse/read model.
- [ ] Support soft warning budgets first. Hard caps require a separate product decision because rejecting executions changes customer-visible behavior.
- [ ] Add export with date range, provider, model family, workflow, and user filters; apply tenant authorization before aggregation.
- [ ] Commit as `feat(admin): add usage and cost visibility`.

**Phase 6 exit gate:** Token/cost totals reconcile with source systems, missing/estimated data is visibly classified, and no prompt/response content is retained for cost reporting.

## Phase 7 — Append-only audit and safe recovery telemetry

### Task 7.1: Introduce the audit ledger after Alembic migration

**Files:**
- Add Alembic migration for `audit_events`
- Create SQLAlchemy audit model/repository/service following the merged persistence layout
- Create: `src/audit/events.py`, `src/audit/redaction.py`, `src/audit/service.py`
- Test: append-only, authorization, redaction, pagination, and export tests

**Interfaces:**
- Produces: `append_audit_event(actor, action, resource, reason, metadata) -> AuditEventId` and read/export APIs.
- Consumes: authenticated actor context and sanitized controlled metadata.

- [ ] Make application credentials append/read-only for audit rows; no update/delete service method exists.
- [ ] Record workflow lifecycle, execution transitions, approvals, cancellations, recovery assessments/actions, role/account changes, API-key/provider-key lifecycle, sharing, deletion, and deployment-setting changes.
- [ ] Store definition hashes/revision references rather than copying full workflow payloads by default.
- [ ] Add retention/export policy and auditor/admin authorization.
- [ ] Prove audit append failure fails closed for privileged recovery/configuration actions but does not rewrite an already-committed execution outcome.
- [ ] Commit as `feat(audit): add append-only business event ledger`.

### Task 7.2: Instrument recovery eligibility and outcomes

**Files:**
- Create recovery assessment/service modules in the post-Alembic execution domain
- Modify owner/admin execution API and UI from Phase 5
- Test safe checkpoint, ambiguous side effect, concurrent transition, cancellation, and audit failure cases

**Interfaces:**
- Produces: `RecoveryAssessment(eligible, reason_code, checkpoint_ref, required_acknowledgements)` and `recover_execution(execution_id, actor, reason, expected_version) -> ExecutionAttempt`.
- Consumes: durable checkpoint metadata, attempt history, side-effect/idempotency classification, and audit service.

- [ ] Allow owners to recover only `eligible=true` failures; admins see but cannot bypass ambiguous safety classification without a separately approved override design.
- [ ] Require a reason and present the failed node, last completed nodes, selected checkpoint, and proposed state patch before confirmation.
- [ ] Keep canceled executions terminal and ineligible.
- [ ] Create a new attempt on the same durable thread; preserve original failed attempt history.
- [ ] Append requested/started/completed/failed recovery audit events and emit bounded telemetry results.
- [ ] Protect recovery/cancel/resume with one conditional state transition and prove only one wins.
- [ ] Commit as `feat(execution): add audited safe recovery`.

**Phase 7 exit gate:** Every recovery is attributable and traceable, no canceled execution can resume, ambiguous side effects cannot be replayed through the normal owner flow, and audit records do not depend on trace sampling.

## Phase 8 — Enterprise hardening and general availability

### Task 8.1: Privacy, load, failure, and tenancy validation

**Files:**
- Create: `tests/security/test_telemetry_redaction.py`
- Create: `tests/integration/test_telemetry_pipeline.py`
- Create: `tests/load/telemetry_execution_load.py`
- Modify: security/privacy/operations documentation and deployment checklists

**Interfaces:**
- Produces: release evidence and rollback criteria.
- Consumes: all prior phases.

- [ ] Seed every secret class and regulated payload class, execute representative workflows, and scan exported spans, metrics, logs, audit rows, and UI responses for leakage.
- [ ] Load-test with telemetry off, 10% traces, and 100% traces; define budgets of less than 5% p95 latency overhead and less than 10% memory overhead at the production concurrency target.
- [ ] Kill/restart/block the collector, exhaust its queue, rate-limit the backend destination, and verify execution correctness plus bounded resource use.
- [ ] Verify tenant/user authorization for telemetry-derived APIs, usage views, audit export, and recovery.
- [ ] Test retention deletion for operational events without deleting the durable audit ledger prematurely.
- [ ] Run complete backend/frontend/E2E/security/container gates and a staging soak of at least one normal retention cycle.
- [ ] Commit as `test(telemetry): prove privacy and failure isolation`.

### Task 8.2: Progressive rollout

- [ ] Enable structured correlation with exporters disabled in development and staging.
- [ ] Enable OTLP traces at 10% sampling in staging, then production canary.
- [ ] Enable metrics at 100% after cardinality and cost review.
- [ ] Enable alerts in notify-only mode; promote paging one alert family at a time after a successful exercise.
- [ ] Release owner UI, then admin diagnostics, then usage/cost, then audited recovery.
- [ ] Publish rollback steps: disable exporter, disable UI feature, disable recovery feature; never roll back Alembic migrations destructively.
- [ ] Update `CHANGELOG.md`, architecture, API reference, operations, privacy, incident-response, and customer-facing telemetry disclosure.
- [ ] Commit as `docs(telemetry): publish rollout and operating model`.

## Required phase reviews

Each phase must pass four gates before the next begins:

1. **Correctness:** focused tests plus the full non-live suite and applicable real-Postgres/E2E tests.
2. **Security/privacy:** signal allow-list and redaction tests prove forbidden data cannot escape.
3. **Operations:** dashboards/runbooks/rollback are usable by someone who did not implement the phase.
4. **Cost/cardinality:** expected time-series, trace volume, log volume, and retention cost are measured in staging.

## Explicitly deferred from this roadmap

- Automatic replay of expired leases or failed executions.
- Recovery of canceled executions.
- Capturing raw prompts, responses, HTTP bodies, uploaded documents, node state, or checkpoint blobs in telemetry.
- User-defined metric names or arbitrary metric labels.
- Hard usage caps until soft-budget telemetry has operated reliably and product semantics are approved.
- A custom metrics backend inside Composer; OTLP destinations and collectors provide storage/aggregation.
- Replacing LangSmith; it remains an optional specialized LLM trace destination.

## Definition of done

The initiative is complete when an operator can detect and diagnose queueing, execution, lease, provider, and recovery failures from dashboards and traces; an owner can understand their execution attempts and safe recovery eligibility; an administrator can inspect operational details and usage without seeing unauthorized payloads; every privileged override is durably audited; telemetry failure cannot affect workflow correctness; and all signals remain portable through OTLP.
