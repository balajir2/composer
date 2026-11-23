"""Safety contract for manually activating telemetry in staging."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/telemetry-staging.yml"


def load_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_staging_activation_is_manual_and_environment_protected() -> None:
    workflow = load_workflow()

    assert set(workflow["on"]) == {"workflow_dispatch"}
    job = workflow["jobs"]["stage-telemetry"]
    assert job["environment"] == "telemetry-staging"
    assert "GCP_STAGING_SA_KEY" in WORKFLOW.read_text(encoding="utf-8")


def test_staging_inputs_require_explicit_target_budget_and_confirmation() -> None:
    inputs = load_workflow()["on"]["workflow_dispatch"]["inputs"]

    for name in (
        "project_id",
        "region",
        "backend_service",
        "artifact_repo",
        "billing_account_id",
        "monthly_budget_usd",
        "confirmation",
    ):
        assert inputs[name]["required"] == "true"
    assert inputs["dry_run"]["default"] == "true"


def test_preflight_rejects_production_and_unconfirmed_or_unbudgeted_runs() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "DEPLOY-STAGING-TELEMETRY" in source
    assert "vars.GCP_PROJECT_ID" in source
    assert "monthly_budget_usd" in source
    assert "billing_account_id" in source
    assert "exit 1" in source


def test_staging_uses_pinned_images_bounded_sampling_and_scale_to_zero() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "backend:${{ github.sha }}" in source
    assert "otel-collector:${{ github.sha }}" in source
    assert "backend:latest" not in source
    assert "otel-collector:latest" not in source
    assert "BACKEND_IMAGE_DIGEST" in source
    assert "COLLECTOR_IMAGE_DIGEST" in source
    assert '--image="$BACKEND_IMAGE_DIGEST"' in source
    assert '--image="$COLLECTOR_IMAGE_DIGEST"' in source
    assert "TELEMETRY_ENABLED=true" in source
    assert "OTEL_TRACES_SAMPLER=parentbased_traceidratio" in source
    assert "OTEL_TRACES_SAMPLER_ARG=0.10" in source
    assert "ENVIRONMENT=staging" in source
    assert "--min-instances=0" in source
    assert "--container=otel-collector" in source


def test_workflow_validates_without_bootstrap_and_records_evidence() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "otelcol-contrib/config.yaml" in source
    assert "validate --config" in source
    assert "gcp-bootstrap.ps1" not in source
    assert '"$BACKEND_URL/ready"' in source
    assert 'print-identity-token --audiences="$BACKEND_URL"' in source
    assert 'readiness.get("status") != "ready"' in source
    assert 'components.get("telemetry") != "ready"' in source
    assert 'trace.get("traceId") != expected_trace' in source
    assert "not trace.get(\"spans\")" in source
    assert "actions/upload-artifact@" in source
    assert "telemetry-staging-evidence" in source


def test_dry_run_cannot_push_images_or_deploy() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["stage-telemetry"]["steps"]

    mutating_steps = [
        step
        for step in steps
        if step.get("name", "").startswith(("Push ", "Deploy "))
    ]
    assert mutating_steps
    assert all("inputs.dry_run == false" in step["if"] for step in mutating_steps)
    assert "inputs.dry_run == 'false'" not in WORKFLOW.read_text(encoding="utf-8")
