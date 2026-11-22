"""Guard the monitoring artifacts against metric drift and unsafe defaults."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "docs/telemetry/signal-catalog.md"
DASHBOARD = ROOT / "deploy/monitoring/composer-dashboard.json"
ALERTS = ROOT / "deploy/monitoring/alerts.yaml"
PROVIDERS = ROOT / "src/telemetry/providers.py"


def catalog_metrics() -> set[str]:
    source = CATALOG.read_text(encoding="utf-8")
    contract = json.loads(source.split("```json\n", 1)[1].split("\n```", 1)[0])
    return {metric["name"] for metric in contract["metrics"]}


def test_dashboard_uses_only_catalogued_metrics_and_has_named_panels() -> None:
    dashboard_text = DASHBOARD.read_text(encoding="utf-8")
    dashboard = json.loads(dashboard_text)
    widgets = dashboard["gridLayout"]["widgets"]
    assert len(widgets) >= 4
    assert all(widget.get("title") for widget in widgets)
    metrics = {
        series["timeSeriesQuery"]["timeSeriesFilter"]["filter"]
        .split('metric.type="', 1)[1]
        .split('"', 1)[0]
        for widget in widgets
        if "xyChart" in widget
        for series in widget["xyChart"]["dataSets"]
        if "timeSeriesFilter" in series.get("timeSeriesQuery", {})
    }
    assert metrics
    catalogued_cloud_names = {
        f"prometheus.googleapis.com/{name}/counter" for name in catalog_metrics()
    } | {f"prometheus.googleapis.com/{name}/histogram" for name in catalog_metrics()}
    assert metrics <= catalogued_cloud_names
    implementation = PROVIDERS.read_text(encoding="utf-8")
    assert all(
        name.rsplit("/", 1)[0].removeprefix("prometheus.googleapis.com/") in implementation
        for name in metrics
    )
    assert "metric.label.result" in dashboard_text
    assert not any(
        identifier in dashboard_text for identifier in ("execution_id", "user_id", "workflow_id")
    )


def test_alerts_are_parseable_owned_and_use_existing_catalog_signals() -> None:
    alerts_text = ALERTS.read_text(encoding="utf-8")
    alerts = yaml.safe_load(alerts_text)
    policies = alerts["alertPolicies"]
    assert policies
    allowed = {f"prometheus.googleapis.com/{name}/counter" for name in catalog_metrics()} | {
        f"prometheus.googleapis.com/{name}/histogram" for name in catalog_metrics()
    }
    for policy in policies:
        assert policy["displayName"]
        assert policy["combiner"] in {"OR", "AND"}
        assert policy["documentation"]["content"].startswith("Runbook: ")
        assert policy["userLabels"]["owner"] == "execution-operations"
        assert policy["userLabels"]["severity"] in {"critical", "warning"}
        assert policy["severity"] in {"WARNING", "CRITICAL"}
        _, anchor = policy["documentation"]["content"].removeprefix("Runbook: ").split("#")
        runbook = (ROOT / "docs/operations/incident-response.md").read_text(encoding="utf-8")
        headings = [
            re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
            for line in runbook.splitlines()
            if line.startswith("#")
        ]
        assert anchor in headings
        for condition in policy["conditions"]:
            threshold = condition["conditionThreshold"]
            assert threshold["duration"]
            assert "metric.type=" in threshold["filter"]
            metric = threshold["filter"].split('metric.type="', 1)[1].split('"', 1)[0]
            assert metric in allowed
            assert metric.rsplit("/", 1)[0].removeprefix(
                "prometheus.googleapis.com/"
            ) in PROVIDERS.read_text(encoding="utf-8")
            assert threshold["comparison"] in {
                "COMPARISON_GT",
                "COMPARISON_GE",
                "COMPARISON_LT",
                "COMPARISON_LE",
            }
    assert not any(
        identifier in alerts_text for identifier in ("execution_id", "user_id", "workflow_id")
    )


def test_monitoring_docs_separate_availability_and_execution_slos() -> None:
    slos = (ROOT / "docs/operations/telemetry-slos.md").read_text(encoding="utf-8")
    assert "Service availability SLI" in slos
    assert "Successful execution SLI" in slos
    assert "burn" in slos.lower()
    assert "minimum" in slos.lower()
    assert "runbook" in slos.lower()


def test_monitoring_document_links_resolve() -> None:
    documents = [
        ROOT / "docs/operations/monitoring.md",
        ROOT / "docs/operations/incident-response.md",
        ROOT / "docs/operations/telemetry-slos.md",
    ]
    pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for document in documents:
        for raw_target in pattern.findall(document.read_text(encoding="utf-8")):
            target = urlsplit(raw_target)
            if target.scheme or target.netloc or not target.path:
                continue
            linked_path = (document.parent / target.path).resolve()
            assert linked_path.exists(), f"{document.relative_to(ROOT)}: missing {raw_target}"
