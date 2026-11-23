# Staging telemetry activation

This runbook activates Composer telemetry only on a no-traffic Cloud Run staging canary. It does not enable production telemetry, change production traffic, or run `scripts/gcp-bootstrap.ps1`.

Use the manually triggered **Stage Composer telemetry** GitHub Actions workflow. Its default mode is validation-only. A deployment requires an exact confirmation value and approval through the protected `telemetry-staging` GitHub environment.

## One-time prerequisites

Create these resources outside the workflow using the organization's normal infrastructure approval process:

- A dedicated GCP project whose ID visibly contains `staging`, `-stage`, or `-stg`. The workflow rejects the configured production project ID.
- An existing Cloud Run backend service and Artifact Registry repository in that project. The service must already have its database, secrets, Cloud Tasks identity, and other non-telemetry application settings.
- A staging service account with only the permissions needed to read the project and budget, push the two images, deploy the staging service, invoke the tagged canary, write telemetry, and read the validation trace.
- A Google Cloud billing budget scoped to the staging project, with at least one threshold rule. Its specified amount must be at least the workflow's `monthly_budget_usd` input.
- A protected GitHub environment named `telemetry-staging`, with required reviewers.
- A `GCP_STAGING_SA_KEY` secret on that environment. Do not reuse the production deployer credential.
- The production project ID stored as the repository or environment variable `GCP_PROJECT_ID`. The workflow uses it as an additional rejection guard.

The workflow reads the budget through the Cloud Billing Budget API. Grant budget-view permission at the billing account only if the staging deployer needs it; do not grant billing administration.

## First run: validation only

From GitHub Actions, select **Stage Composer telemetry**, choose **Run workflow**, and supply:

- `project_id`: dedicated staging project
- `region`: staging Cloud Run and Artifact Registry region
- `backend_service`: existing staging backend service
- `artifact_repo`: existing staging image repository
- `billing_account_id`: billing account containing the staging budget
- `monthly_budget_usd`: approved positive staging limit
- `confirmation`: any non-secret placeholder for a dry run
- `dry_run`: `true`

The dry run authenticates to staging, rejects unsafe inputs, verifies the service and budget alert, builds both images locally, and validates the exact collector image. It cannot push images or deploy.

Review the `telemetry-staging-evidence` artifact and workflow summary. Resolve every failed preflight instead of weakening a guard.

## Canary run

Repeat the workflow with:

- `confirmation`: exactly `DEPLOY-STAGING-TELEMETRY`
- `dry_run`: `false`

The workflow then:

1. Pushes backend and collector images tagged with the Git commit SHA, resolves their registry
   digests, and deploys those immutable digest references.
2. Creates a `telemetry-staging` tagged Cloud Run revision with no production traffic.
3. Keeps minimum instances at zero.
4. Enables OTLP export to the loopback collector with parent-based 10% trace sampling.
5. Calls `/ready` with a forced sampled W3C trace context.
6. Requires that exact trace to appear in Cloud Trace within one minute.
7. Uploads readiness, budget, canary URL, and trace evidence for 30 days.

The deployment updates only telemetry environment variables on the application container. Existing staging database, secret, and application settings remain on the service. Telemetry failure remains non-blocking for application readiness, but missing validation telemetry fails this staging workflow.

## Observation and decision

Leave production telemetry disabled. Observe the staging canary for 24-48 hours with representative test traffic, then record:

- Cloud Run collector CPU and memory use
- log bytes, metric volume, and trace spans
- estimated monthly cost at representative traffic
- provider and WebSocket dashboard behavior
- collector restarts, rejected exports, retry exhaustion, or queue pressure
- label cardinality and any `other` mapping above the documented threshold
- a privacy sample confirming that secrets and customer payloads are absent

Use the thresholds in [monthly telemetry maintenance](telemetry-monthly-maintenance.md). A production canary requires a separate reviewed change and explicit approval; this workflow has no traffic-promotion step.

## Cleanup

If the staging canary is no longer needed, remove the tagged revision through the approved Cloud Run change process. Keep the evidence artifact and cost record. Do not delete the shared staging service, database, budget, or Artifact Registry repository as part of telemetry cleanup.
