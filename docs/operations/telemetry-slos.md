# Telemetry SLIs and SLOs

**Owner:** execution operations. These objectives apply to production and are reviewed monthly. The SLO targets are operational objectives, not customer contract terms; customer commitments remain in [the SLA](../saas/sla.md).

## Service availability SLI

**SLI:** successful external HTTPS checks of the production `/health` endpoint divided by all scheduled checks, measured at one-minute intervals. A success is an HTTP 200 response before the probe timeout. This measures process reachability only; `/health` is not database-aware readiness.

**SLO:** at least 99.9% successful checks over each rolling 30-day window. The external uptime monitor is the source of truth because the current application telemetry package does not emit HTTP request counters or latency histograms. A five-minute outage is an immediate investigation trigger; the monthly objective is assessed from the probe history.

## Successful execution SLI

**SLI:** completed executions divided by completed plus failed executions in the same UTC window. Canceled executions are excluded because cancellation is an intentional terminal outcome. Waiting approvals and queued/running executions are not yet outcomes and are excluded until terminal.

**SLO:** at least 99.0% successful executions over each rolling 30-day window, with a minimum of 100 terminal executions before publishing a percentage. Below the minimum, report the sample count and mark the window insufficient; do not infer success from zero failures.

The eventual telemetry expression uses only the catalogued `composer.execution.terminal` counter grouped by its bounded `status` label (`completed` and `failed`). That instrument is not currently emitted. Until it is enabled and staging data validates the calculation, use the aggregate database query below; never use trace volume as the denominator. It matches terminal completion times in a UTC window and returns aggregate status counts only:

```sql
SELECT status, count(*) AS executions
FROM workflow_executions
WHERE status IN ('completed', 'failed')
  AND completed_at >= :start
  AND completed_at < :end
GROUP BY status;
```

The successful ratio is `completed / (completed + failed)`. No current dashboard alert claims to measure this SLO. For the broader execution count, duration, and failure definitions, see [the signal catalog](../telemetry/signal-catalog.md#baseline-query-definitions).

## Burn-rate response

For a 99.0% successful-execution objective, the allowed error budget is 1%. Once terminal metrics are emitted, review both a 1-hour and 6-hour burn window. Escalate to Sev-2 if the failure ratio exceeds 14.4% in both windows and the combined window contains at least 100 terminal executions (14.4x budget burn). Escalate to Sev-1 if it exceeds 36% in both windows and the combined window contains at least 100 executions (36x burn). This guard prevents one failure in a low-traffic service from paging. These thresholds are documented policy only until the terminal metric is active.

Provider and WebSocket telemetry has independent burst alerts in [`alerts.yaml`](../../deploy/monitoring/alerts.yaml). The alert policies require repeated failures in a 5-minute window or a larger aggregate count in 1 hour; these are minimum-count guards, not SLO burn alerts. Provider calls are not execution outcomes, so provider health does not substitute for the successful-execution SLI.

## Current measurement limits

The live telemetry implementation emits `composer.provider.request`, `composer.provider.request.duration`, and the four catalogued WebSocket metrics. It does not emit HTTP request metrics, `composer.execution.terminal`, queue age, claim failures, exporter loss/health, or token/cost metrics. Do not create filters or alerts for those absent signals. The collector's retry queue and health endpoint are diagnostics, not application metrics; a missing exporter-health metric cannot be turned into a metric alert.

Metric labels remain bounded by the catalog. Do not add execution, request, user, workflow, provider-payload, URL, or error-message identifiers to a dashboard query or alert filter.

## Runbooks and deployment

- [Monitoring overview](monitoring.md#google-cloud-telemetry)
- [Provider alert response](incident-response.md#provider-alerts)
- [WebSocket delivery alert response](incident-response.md#websocket-alerts)
- [Execution and availability incident triage](incident-response.md#telemetry-slo-triage)

Import the dashboard with `gcloud monitoring dashboards create --config-from-file=deploy/monitoring/composer-dashboard.json`. Create policies from the YAML with `gcloud monitoring policies create --policy-from-file=deploy/monitoring/alerts.yaml`. Set the project on the gcloud configuration before importing. Exercise the alerts in staging and record the incident IDs and metric evidence in the deployment checklist before enabling paging.
