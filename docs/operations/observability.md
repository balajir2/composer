# Observability Runbook

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Audience:** SREs, on-call engineers, anyone debugging production behaviour.
> **Companion docs:** [monitoring.md](monitoring.md) for the alarm setup, [incident-response.md](incident-response.md) for what to do when alarms fire, [scaling.md](scaling.md) for the capacity-related signals.

This runbook describes **what to look at, where to look, and what each signal means**. It's the reference for "I see X, what does it mean and where did it come from?"

## The three pillars

Composer's observability fits the standard three-pillar model:

| Pillar | What it answers | Where it lives |
|---|---|---|
| **Logs** | What happened, in human-readable detail | Backend/frontend host logs and Postgres service logs |
| **Metrics** | How much, how fast, how often | Host metrics and operator-defined DB/log-derived measurements |
| **Traces** | What did a single workflow execution actually do | LangSmith (when enabled) |

The pillars complement each other. Most investigations start with metrics ("what changed?"), drill into traces ("what happened in that one bad run?"), and confirm with logs ("what was the exact error message?").

## Logs

### Backend logs

Composer uses Python's `logging` module. The application does not install a universal JSON formatter or guarantee the enrichment fields below on every record; configure structured formatting and request correlation at the deployment layer if your operations standard requires them. Common application context includes:

| Field | Meaning |
|---|---|
| `level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `logger` | Module path (e.g., `src.api.executions`) |
| `timestamp` | ISO-8601 UTC |
| `message` | Human-readable text |
| user/execution/workflow identifiers | Present in selected messages, not guaranteed as structured fields on every record |

Log level is controlled by the `LOG_LEVEL` env var. Production deployments default to `INFO`. `DEBUG` produces request-body logs that may include execution input — only enable temporarily during investigation, not as a permanent setting.

Common log lines and what they mean:

| Pattern | Meaning |
|---|---|
| `Starting Composer v<x.y.z> in production mode` | Backend booted; record the version |
| `auth: dev-mode fallback ENABLED` | `ENVIRONMENT` isn't set to `production` — dangerous in real deployments |
| `Execution <id> failed` | Workflow execution raised an exception; details follow with full traceback |
| `Execution <id> paused at user-approval node` | Interrupt-resume cycle began; expect a `/resume` call later |
| `execution_sweeper: started (interval=300s, orphan_grace=120s, approval_timeout=0h)` | Local maintenance loop boot with default settings; it handles lease-less orphan attempts and opt-in approval expiration. Cloud Run normally disables this loop and uses `POST /internal/sweep` request logs instead. |
| `execution_sweeper: disabled (interval_seconds=0)` | The local loop is disabled; expected when Cloud Scheduler drives production maintenance. |
| `execution_sweeper: scanned N orphaned attempts, F failed, E errored` | Lease-less running attempts older than their current attempt's claim grace were selected. `F` counts successful conditional failures with `failureCode=orphaned_running`; lost races count as scanned but not failed. |
| `execution_sweeper: scanned N expired leases, F failed, E errored` | Running attempts with expired processing leases were selected. `F` counts successful conditional failures with `failureCode=lease_expired`; a concurrently renewed live lease must survive. This runs through `/internal/sweep` or an explicit full maintenance call, not the local fallback loop. |
| `execution_sweeper: scanned N approval pauses, F expired, E errored` | Opt-in approval expiration ran; `APPROVAL_WAIT_TIMEOUT_HOURS=0` disables this by default. |
| `POST /internal/sweep` request / response | Cloud Scheduler's production maintenance endpoint. Inspect `orphaned_running`, `leases_expired`, `approvals_expired`, and `events_deleted`; a `null` component signals failure even when HTTP status is 200. |
| `internal: sweep_orphaned_running failed` / `internal: sweep_expired_leases failed` | A maintenance component raised; inspect the traceback and corresponding `null` response field. A successful HTTP delivery alone does not prove all sweep components succeeded. |
| `internal: OIDC verification failed: ...` | A push request to `/internal/claim-and-run` or `/internal/sweep` presented an invalid/wrong-audience OIDC token — check the Cloud Tasks queue's / Cloud Scheduler job's service account matches `CLOUD_TASKS_SERVICE_ACCOUNT` |
| `cloud_tasks: in-process fallback failed for execution <id>` | `enqueue_execution()`'s dev-mode fallback (no `CLOUD_TASKS_SERVICE_ACCOUNT` configured) crashed running the execution in-process — expected only outside production |
| `internal: claim_and_run crashed for execution <id> (kind=<kind>)` | The request worker crashed; inspect the traceback and guarded terminal write. An already-resolved execution is preserved. |
| `MCPClient: tools/list returned <N> tools` | MCP server enumeration succeeded |
| `MCP base64 blob stripped from response` | Sanitiser fired on an MCP response — usually fine |

Sweep summary lines appear only when rows were selected; their absence does not prove maintenance stopped. Check the configured `composer-sweep` job's dispatch history and `/internal/sweep` component results. Failures never enqueue automatic lease replay. An execution's original age is not an abandonment signal: a live lease or fresh resumed `runningSince` protects a healthy attempt.

### Postgres logs

Long-running queries, deadlocks, and slow execution plans appear here. Keep an eye on:

| Pattern | Meaning | Action |
|---|---|---|
| `LOG: duration: <N>ms statement: ...` | Slow query | If routine, the workload may need an index. If sudden, check for plan regression. |
| `ERROR: deadlock detected` | Two transactions waiting on each other | Usually transient; investigate if recurring |
| `WARNING: connections to be terminated due to administrator command` | Host is restarting — typically host maintenance | Coordinate with host's status page |

Most production setups don't need to read Postgres logs directly; the host's UI surfaces the relevant signals.

### Frontend host logs

Cloud Run is the current deployment target; Vercel is an alternate. The selected host's runtime logs capture frontend rendering and NextAuth failures. Useful for:

- Rendering failures on the canvas (rare; most errors come from backend responses)
- NextAuth callback failures during SSO
- 502/504 from the frontend's API proxy when the backend is misbehaving

## Metrics

### From the application

Composer doesn't ship its own metrics-exporter today (no Prometheus scrape endpoint). Metrics are derived from logs + DB queries.

| Metric | How to compute it | What good looks like |
|---|---|---|
| Request rate | Count `POST /executions` per minute from logs | Stable; spikes correlate with customer activity |
| Execution success rate | `count(status='completed') / count(*)` per hour from `workflow_executions` | >95% (lower means systemic issue) |
| Sev-1 incidents per month | Count of postmortems tagged Sev-1 | Trending toward zero |
| Abandoned-attempt failures | Sum successful `orphaned_running` and `leases_expired` counts from `/internal/sweep`, or the corresponding `F failed` log values; avoid counting both sources twice | Trending toward zero; investigate `errored` values or `null` response components separately |
| Mean execution duration | `avg(completed_at - started_at)` per workflow | Workflow-specific; alarm on sudden 2× regression |
| External-invoke 401 rate | Count of `Authorization: Bearer ck_...` 401s per hour | <1% of api_run requests |

### From the host

The container host (Fly / Render / Cloud Run) provides:

- **CPU utilisation** per replica
- **Memory utilisation** per replica
- **Request count + latency** at the load balancer
- **Health check pass rate**

Tune alarm thresholds per [monitoring.md](monitoring.md). For most deployments:
- CPU > 80% sustained for 10 min → warning
- Memory > 85% sustained for 5 min → warning
- Health check pass rate < 99% over 15 min → page

### From Postgres (Neon UI or equivalent)

- **Connection count** vs. plan limit
- **CPU on the compute node**
- **Storage used**
- **Replication lag** (if you have read replicas)
- **Cache hit ratio** (Postgres-level; not the host)

Neon's UI shows all of these in real time.

### From LangSmith

- **Trace count per project** per hour
- **Errored runs** count
- **Token usage by model**
- **Latency distribution** (p50 / p95 / p99) per workflow

These are the highest-signal metrics for "is the LLM side healthy?"

## Traces

LangSmith captures full execution traces when `LANGCHAIN_TRACING_V2=true`. A trace shows:

- Every prompt that went to an LLM, with token counts and latency
- Every tool call, with arguments and result
- The branch taken at each conditional node
- Exceptions, with the full stack trace
- Total wall-clock time + token spend

To find a specific trace:

1. Open the LangSmith project for the deployment
2. Filter by `metadata.execution_id == <id>` (we propagate the Composer execution id into LangSmith metadata)
3. Click the trace; it expands into a tree

For workflows that fail intermittently, sort by error rate and click into a recent failure. The trace usually shows the issue immediately — a malformed tool call, a misshapen agent response, a slow upstream API.

## Tying the three together

A typical investigation flow:

1. **Alarm fires** (metric): "execution success rate < 90% for 30 min"
2. **Find the affected executions**: `SELECT id, error FROM workflow_executions WHERE status='failed' AND started_at > now() - interval '1 hour' ORDER BY started_at DESC LIMIT 20;`
3. **Pattern in the errors?** If yes (e.g., "ChatAnthropicError: 503"), the issue is the LLM provider. Check the provider's status page; if down, comms.
4. **No clear pattern?** Pick one failed execution. Open its LangSmith trace.
5. **Trace shows the failure point?** Investigate the specific node — bad config, malformed input, dependency issue.
6. **Trace doesn't show it?** Drop into backend logs filtered to the execution_id: `grep -F 'execution_id":"<id>"' backend.log`. The full Python traceback will be there.
7. **Mitigation** per [incident-response.md](incident-response.md). **Postmortem** afterwards.

## Common dashboards

If you're standing up dashboards, the Composer-specific ones we recommend:

### "Composer health overview"

A single dashboard showing:
- Backend uptime (per the external monitor)
- Execution rate per minute
- Execution success rate (last hour)
- p95 execution latency
- Orphaned-running and expired-lease failure counts (should be ~0)
- LangSmith trace volume (should match execution rate)
- Top 10 most-failed workflows in the last 24h

### "Workflow performance"

For deep-dive on a specific workflow:
- Execution count over time
- Success rate
- p50 / p95 / p99 latency
- Token spend (from LangSmith)
- Top error messages

### "Sub-processor health"

How dependent are we on each sub-processor right now?
- Anthropic call count + error rate
- OpenAI call count + error rate
- Each enabled tool provider's call count + error rate
- Each MCP server's call count + error rate

A sudden swing in any of these is signal that something downstream changed.

## What's not yet observable

Honest about gaps:

- **No per-customer cost attribution.** We can't (yet) tell you "Customer X cost $Y in tokens last month" without manual SQL across LangSmith + Composer rows. On the [`../saas/roadmap.md`](../saas/roadmap.md).
- **No real-time per-execution token counter** during the run. LangSmith shows it after the fact.
- **No SLO dashboards** that aggregate across plan tiers. The SLA is computed per-deployment via the external monitor; cross-deployment aggregation is manual today.
- **No vendor-neutral application telemetry pipeline.** There is no OpenTelemetry SDK/exporter, Prometheus endpoint, or built-in trace/metric backend. Host telemetry and optional LangSmith tracing must not be described as complete application observability.
- **No immutable administrator audit ledger.** Execution events and business rows are useful operational evidence, but administrative changes are not all recorded in an append-only, exportable audit table.

## Adjacent docs

- [monitoring.md](monitoring.md) — alarm + paging setup
- [incident-response.md](incident-response.md) — what to do when an alarm fires
- [scaling.md](scaling.md) — capacity-related signals
- [`../decisions.md`](../decisions.md) — ADR-0003 (LangSmith tracing config threading) is the source of truth on how traces flow

## Telemetry contract roadmap

The [signal catalog](../telemetry/signal-catalog.md) and [privacy and cardinality contract](../telemetry/privacy-and-cardinality.md) specify future vendor-neutral instrumentation and baseline query definitions. These instruments, distributed correlation and centralized redaction are not implemented yet. Existing host/log-derived measurements and optional LangSmith tracing remain the current capabilities; LangSmith may capture sensitive payloads and requires appropriate destination controls. Operational telemetry, durable audit and privileged recovery remain separate systems.
