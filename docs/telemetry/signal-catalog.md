# Telemetry signal catalog

This is the binding **roadmap target**, not an inventory of emitted instruments. The canonical names, types, units and attribute sets come from the [roadmap](../superpowers/plans/2026-11-20-telemetry-roadmap.md). Every metric below is owned by execution operations, uses the operational retention class (finite retention, deployment policy to be set before export), and has the bounded-metadata redaction classification. Never export a value until it has been mapped to the listed vocabulary; map unknown values to `other` where present. A missing failure code maps to `none`. `model_family` is deliberately restricted to `other` until a reviewed finite mapping exists; never emit raw model names. Controlled vocabularies include contract-level aggregation values and do not create runtime execution states.

## Machine-readable contract

```json
{
  "vocabularies": {
    "trigger_kind": [
      "run",
      "resume",
      "other"
    ],
    "environment": [
      "development",
      "test",
      "production",
      "other"
    ],
    "status": [
      "queued",
      "running",
      "waiting_approval",
      "completed",
      "failed",
      "canceled",
      "other"
    ],
    "failure_code": [
      "execution_error",
      "execution_timeout",
      "orphaned_running",
      "lease_expired",
      "none",
      "other"
    ],
    "result": [
      "success",
      "failure",
      "scanned",
      "marked_failed",
      "errored",
      "deleted",
      "other"
    ],
    "node_type": [
      "start",
      "end",
      "note",
      "file-trigger",
      "file-write",
      "agent",
      "mcp",
      "http",
      "extract",
      "transform",
      "data-transform",
      "set-state",
      "if-else",
      "decision",
      "while",
      "user-approval",
      "guardrails",
      "vector-db",
      "gamma-ai",
      "email",
      "arcade",
      "join-chunks",
      "jira",
      "confluence",
      "download-pdf",
      "join",
      "other"
    ],
    "provider": [
      "anthropic",
      "openai",
      "google",
      "groq",
      "deepseek",
      "qwen",
      "langsmith",
      "tavily",
      "firecrawl",
      "serper",
      "browserless",
      "gamma",
      "resend",
      "typesafe",
      "other"
    ],
    "operation": [
      "invoke",
      "tool",
      "other"
    ],
    "decision": [
      "approve",
      "reject",
      "other"
    ],
    "expired": [
      "true",
      "false"
    ],
    "eligibility_reason": [
      "eligible",
      "other"
    ],
    "model_family": [
      "other"
    ],
    "token_type": [
      "input",
      "output",
      "cached",
      "other"
    ],
    "cost_source": [
      "provider",
      "estimated",
      "other"
    ],
    "component": [
      "orphaned_running",
      "leases_expired",
      "approvals_expired",
      "events_deleted",
      "other"
    ]
  },
  "metrics": [
    {
      "name": "composer.execution.started",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{execution}",
      "attributes": [
        "trigger_kind",
        "environment"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.terminal",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{execution}",
      "attributes": [
        "status",
        "failure_code",
        "trigger_kind"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.attempt.duration",
      "owner": "execution-operations",
      "type": "Histogram",
      "unit": "s",
      "attributes": [
        "result",
        "failure_code"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.queue_wait",
      "owner": "execution-operations",
      "type": "Histogram",
      "unit": "s",
      "attributes": [
        "trigger_kind",
        "result"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.node.duration",
      "owner": "execution-operations",
      "type": "Histogram",
      "unit": "s",
      "attributes": [
        "node_type",
        "result"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.approval_wait",
      "owner": "execution-operations",
      "type": "Histogram",
      "unit": "s",
      "attributes": [
        "decision",
        "expired"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.lease_expired",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{attempt}",
      "attributes": [
        "failure_code"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.orphaned",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{attempt}",
      "attributes": [
        "failure_code"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.execution.recovery",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{attempt}",
      "attributes": [
        "result",
        "eligibility_reason"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.provider.request.duration",
      "owner": "execution-operations",
      "type": "Histogram",
      "unit": "s",
      "attributes": [
        "provider",
        "operation",
        "result"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.provider.request",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{request}",
      "attributes": [
        "provider",
        "operation",
        "result"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.llm.tokens",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{token}",
      "attributes": [
        "provider",
        "model_family",
        "token_type"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.llm.cost",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "USD",
      "attributes": [
        "provider",
        "model_family",
        "cost_source"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    },
    {
      "name": "composer.maintenance.sweep",
      "owner": "execution-operations",
      "type": "Counter",
      "unit": "{item}",
      "attributes": [
        "component",
        "result"
      ],
      "retention": "operational",
      "redaction": "bounded-metadata"
    }
  ]
}
```

## Inventory and interpretation

- Execution statuses are `queued`, `running`, `waiting_approval`, `completed`, `failed`, `canceled`; the first three are active, the latter three terminal. Cancellation is terminal, never a recovery or replay signal. Sources: `src/api/execution_status.py`, `src/api/run.py`.
- Concrete persisted failure codes: `execution_error`, `execution_timeout`, `orphaned_running`, `lease_expired` (`src/engine/langgraph_executor.py`, `src/api/internal.py`, `src/maintenance/execution_sweeper.py`). Some failures, including enqueue and approval expiry, have no failure code; use `none`.
- There is no persisted trigger-kind enum. Worker delivery kinds are `run` and `resume` (`src/api/internal.py`); `trigger_kind` uses these bounded values as a delivery classification, not an invented execution status or initiator identity.
- Node types are the `NODE_CLASSES` keys in `src/builder/builder_tools.py`. The provider list includes the six LLM dispatch providers in `src/llm/providers.py` and the additional managed key providers in `src/api/admin_llm_keys.py`; `other` covers any other tool provider.
- Maintenance components correspond to `/internal/sweep` response fields. Sweep results include `scanned`, `marked_failed`, `errored`, and event `deleted`; races can be scanned without being marked failed. A null component denotes failure, not zero. `success` and `failure` are aggregation outcomes, not stored states.
- `operation`, `result`, `decision`, `expired`, `eligibility_reason`, `token_type`, `cost_source`, and `environment` are finite telemetry vocabularies. Recovery is a future capability; `eligible` is a telemetry classification, not an existing recovery state. New categories require contract review.

## Span names and correlation

Target spans: `composer.execution.enqueue`, `composer.execution.claim`, `composer.execution.attempt`, `composer.checkpoint.load`, `composer.node.execute`, `composer.provider.call`, `composer.tool.call`, `composer.checkpoint.save`, and `composer.event.persist`, under the HTTP request. Cloud Tasks breaks synchronous parentage: propagate W3C context in task headers and link delayed/retried deliveries by opaque execution/attempt correlation. `request_id`, `workflow_id`, `execution_id`, `thread_id`, `attempt_id`, and `node_id` may appear in access-controlled traces and logs, never metrics; attempt IDs are future work. Apply the [privacy contract](privacy-and-cardinality.md) before export.

## Baseline query definitions

These PromQL templates describe future OTLP-to-Prometheus translation (dots become underscores, counters gain `_total`, histogram buckets gain `_bucket`); the backend and exporter do not exist yet. Record definitions only, never customer results. Use a 5-minute window and aggregate away labels:

| Question | Query |
|---|---|
| Execution count | `sum(increase(composer_execution_started_total[5m]))` |
| Failure count | `sum(increase(composer_execution_terminal_total{status="failed"}[5m]))` |
| p50 attempt duration | `histogram_quantile(0.50, sum by (le) (rate(composer_execution_attempt_duration_seconds_bucket[5m])))` |
| p95 attempt duration | `histogram_quantile(0.95, sum by (le) (rate(composer_execution_attempt_duration_seconds_bucket[5m])))` |
| Lease expiry | `sum(increase(composer_execution_lease_expired_total[5m]))` |
| Provider failure rate | `sum by (provider) (rate(composer_provider_request_total{result="failure"}[5m])) / sum by (provider) (rate(composer_provider_request_total[5m]))` |

For provider rate, a zero denominator means no data, not zero failures. Verify actual exporter name/unit translation before deployment.
