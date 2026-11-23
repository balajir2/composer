# Monthly telemetry maintenance

**Owner:** execution operations

**Frequency:** once per calendar month and after any material telemetry or deployment change

**Expected time:** 30–45 minutes
**Record:** keep the completed record at the bottom of this document or in the team's operations log

This runbook keeps Composer telemetry useful, affordable, and safe. It covers the OpenTelemetry application runtime, Cloud Run collector sidecar, Google Cloud telemetry destinations, dashboards, alerts, and the `/ready` endpoint. It does not replace incident response, database maintenance, durable execution audit, or recovery procedures.

If production telemetry is disabled, complete the cost, configuration-drift, dependency, privacy, and readiness checks. Mark signal-dependent checks `not applicable — telemetry disabled`; do not enable telemetry merely to complete this checklist.

## Before starting

- Confirm the production project and environment. Never inspect or modify a different customer or staging project by mistake.
- Open the [Composer telemetry dashboard](../../deploy/monitoring/composer-dashboard.json), Google Cloud Billing reports, Cloud Logging, Cloud Monitoring, and Cloud Trace.
- Use a rolling 30-day window and compare it with the preceding 30 days. Record absolute values as well as percentage changes; a large percentage over a tiny sample is not an incident.
- Review the [telemetry SLO definitions and measurement limits](telemetry-slos.md) before interpreting missing signals.

## Monthly checklist

### 1. Cost and volume

- [ ] Record total Google Cloud spend for the current and preceding 30-day windows.
- [ ] Record Cloud Run CPU, memory, request, and active-instance cost for the Composer backend.
- [ ] Record billed log bytes, OTLP metric samples or bytes, and trace spans ingested and scanned.
- [ ] Confirm the billing budget and forecast alerts are enabled and reach an actively monitored recipient.
- [ ] Investigate any telemetry-related cost increase greater than **20%** or **USD 5**, whichever is larger, unless the increase is explained by comparable growth in production traffic.
- [ ] If telemetry exceeds its agreed monthly budget, reduce trace sampling or unnecessary log volume first. Do not remove error telemetry, bounded operational metrics, redaction, or audit records to reduce cost.

When telemetry is enabled, keep normal successful-request traces sampled at the approved rate. Retain error traces at the highest practical rate. Any sampling change requires a short record of the old value, new value, reason, date, and approver.

### 2. Service health and SLOs

- [ ] Confirm the external `/health` availability SLI meets the 99.9% rolling 30-day objective.
- [ ] Confirm `/ready` returns HTTP 200 and reports database `ready`, schema `current`, and runtime `ready`.
- [ ] Review successful execution results using the source and minimum-sample rules in [telemetry-slos.md](telemetry-slos.md).
- [ ] Review provider failure and latency panels, WebSocket delivery panels, and alert history.
- [ ] Investigate any five-minute availability interruption, breached SLO, repeated alert, or unexplained gap in expected telemetry.

Telemetry being `disabled` is valid when production telemetry has not been activated. Telemetry being `degraded` does not block readiness, but it requires investigation when telemetry is expected to be enabled.

### 3. Collector and export path

- [ ] Confirm the deployed collector image version matches [`deploy/otel-collector/Dockerfile`](../../deploy/otel-collector/Dockerfile).
- [ ] Check collector logs for authentication failures, rejected data, queue saturation, retry exhaustion, out-of-memory termination, or restart loops.
- [ ] Confirm traces, metrics, and logs have arrived recently for a known production request without searching for payload content or secrets.
- [ ] Confirm the collector still listens only on loopback for OTLP and health endpoints.
- [ ] Review available collector releases and security advisories. Upgrade through a pull request and staging validation; never change the production image tag manually.

Take action immediately if exports have been absent for more than **30 minutes** during known traffic, the collector repeatedly restarts, or authentication/rejection errors persist for more than **15 minutes**.

### 4. Cardinality and data quality

- [ ] Review the highest-volume metrics and time-series counts for sudden growth.
- [ ] Confirm metric labels remain within the bounded vocabulary in the [signal catalog](../telemetry/signal-catalog.md).
- [ ] Confirm metric labels do not contain request, execution, workflow, thread, attempt, node, user, URL, provider-payload, or error-message identifiers.
- [ ] Check for unknown or `other` values that exceed **5%** of a signal with at least 100 observations; investigate mapping drift before expanding the vocabulary.
- [ ] Investigate a time-series-count increase greater than **25%** that is not explained by a reviewed signal change.

Do not add raw model names, customer-defined values, identifiers, or exception messages to metric labels. High-cardinality values belong only in access-controlled traces or logs where the privacy contract permits them.

### 5. Privacy, access, and retention

- [ ] Sample recent logs and traces from successful and failed requests and confirm that authorization headers, cookies, tokens, API keys, connection strings, prompts, model responses, uploaded content, node inputs/outputs, email content, and raw exception text are absent.
- [ ] Confirm destination access is still limited to approved operators and service accounts.
- [ ] Review IAM changes involving telemetry writer, logging, monitoring, trace, or service-usage roles.
- [ ] Confirm log, metric, and trace retention matches the approved operational retention policy.
- [ ] Confirm no log sink, trace scope, metric scope, or export route was added without review.

Treat any discovered credential, secret, customer payload, or personal data in telemetry as a security incident: restrict access, preserve only necessary evidence, rotate exposed credentials where applicable, and follow the [incident-response runbook](incident-response.md). Do not paste sensitive samples into the maintenance record.

### 6. Dashboards, alerts, and runbooks

- [ ] Confirm every dashboard panel either shows current data or clearly states that the signal is not yet emitted.
- [ ] Confirm alert policies are enabled, have valid notification channels, and still link to existing runbook anchors.
- [ ] Review alerts that fired during the month and record false positives, missed incidents, or thresholds needing adjustment.
- [ ] Exercise alerts in staging after policy or signal changes. Do not deliberately trigger production incidents for routine validation.
- [ ] Confirm the dashboard and alert definitions deployed in Google Cloud match the versioned files under [`deploy/monitoring`](../../deploy/monitoring/).

Do not create dashboards or alerts for signals listed as absent in [telemetry-slos.md](telemetry-slos.md). A blank graph for a nonexistent instrument is not evidence of good health.

### 7. Dependency and configuration drift

- [ ] Review OpenTelemetry SDK, instrumentation, exporter, and collector release notes for security or compatibility fixes.
- [ ] Confirm `TELEMETRY_ENABLED`, the OTLP endpoint, service name, environment name, and sampling configuration match the approved production configuration.
- [ ] Confirm telemetry remains non-blocking: an exporter or collector failure must not prevent application startup or make `/ready` fail.
- [ ] Confirm no application or collector configuration bypasses the [privacy and cardinality contract](../telemetry/privacy-and-cardinality.md).
- [ ] Create separate, reviewable issues or pull requests for required updates. Validate collector configuration and application tests before deployment.

## Escalation guide

| Finding | Required response |
| --- | --- |
| Secret, credential, customer payload, or personal data exported | Start security incident response immediately; restrict access and rotate exposed credentials as applicable. |
| `/ready` reports database unavailable or schema mismatch | Treat as a deployment/database incident; do not classify it as a telemetry-only problem. |
| SLO breach or confirmed production outage | Follow the incident-response severity and communication process. |
| Export gap over 30 minutes during known traffic | Investigate application runtime, collector, IAM, quota, and destination health. |
| Persistent collector authentication or rejection errors over 15 minutes | Escalate to the on-call owner and correct IAM, quota, or exporter configuration through a reviewed change. |
| Telemetry cost increase over the monthly threshold | Identify the signal and volume driver; adjust sampling or unnecessary verbosity through a reviewed change. |
| Cardinality increase over 25% or unknown values over 5% | Investigate instrumentation drift before changing dashboards or vocabularies. |
| Dependency security advisory | Assess severity the same business day and schedule or apply the update according to security policy. |

## Maintenance record template

Copy this block for each review. Records must contain aggregates and links only—never secrets, payloads, or customer data.

```text
Review month:
Completed at (UTC):
Operator:
Production project/environment:
Telemetry enabled: yes / no

Cost and volume:
- Total GCP spend, current / previous:
- Telemetry-related spend, current / previous:
- Logs ingested:
- Metric volume:
- Trace spans ingested / scanned:
- Budget or forecast alerts:

Health:
- /health availability:
- /ready result:
- Successful execution SLI and sample count:
- Provider/WebSocket alerts reviewed:

Safety and quality:
- Export health:
- Cardinality findings:
- Privacy sample result:
- IAM/retention changes:
- Dependency or collector updates required:

Actions opened (owner and due date):
Exceptions accepted (approver and expiry date):
Evidence links:
Final status: healthy / action required / incident opened
```

The monthly review is complete only when every applicable item is checked, every exception has an owner and expiry date, and material findings have a tracked action or incident.
