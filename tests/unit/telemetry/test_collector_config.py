"""Production safety contract for the Cloud Run OTLP collector sidecar."""

from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(__file__).resolve().parents[3] / "deploy/otel-collector/config.yaml"


def load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_collector_receives_otlp_only_on_instance_loopback() -> None:
    receiver = load_config()["receivers"]["otlp"]["protocols"]

    assert receiver["grpc"]["endpoint"] == "127.0.0.1:4317"
    assert receiver["http"]["endpoint"] == "127.0.0.1:4318"


def test_each_signal_has_memory_limit_batch_and_google_export() -> None:
    config = load_config()

    assert config["processors"]["memory_limiter"]["limit_mib"] > 0
    assert config["processors"]["memory_limiter"]["spike_limit_mib"] > 0
    assert config["processors"]["batch"]["send_batch_size"] > 0
    assert config["processors"]["batch"]["send_batch_max_size"] > 0

    for signal in ("traces", "metrics", "logs"):
        pipeline = config["service"]["pipelines"][signal]
        assert pipeline["receivers"] == ["otlp"]
        assert "memory_limiter" in pipeline["processors"]
        assert "batch" in pipeline["processors"]
        assert "resourcedetection" in pipeline["processors"]
        assert "resource/gcp_project_id" in pipeline["processors"]
        assert pipeline["exporters"]


def test_resources_add_project_and_composer_metadata() -> None:
    processors = load_config()["processors"]

    project_attributes = processors["resource/gcp_project_id"]["attributes"]
    composer_attributes = processors["resource/composer"]["attributes"]
    assert any(attribute["key"] == "gcp.project_id" for attribute in project_attributes)
    assert any(attribute["key"] == "service.namespace" for attribute in composer_attributes)
    assert any(
        attribute["key"] == "deployment.environment.name" for attribute in composer_attributes
    )


def test_google_cloud_project_and_environment_are_config_inputs() -> None:
    config_text = CONFIG.read_text(encoding="utf-8")

    assert "${env:GOOGLE_CLOUD_PROJECT}" in config_text
    assert "${env:ENVIRONMENT:-production}" in config_text


def test_network_exporters_have_bounded_queues_and_retries() -> None:
    for exporter in load_config()["exporters"].values():
        assert exporter["sending_queue"]["enabled"] is True
        assert exporter["sending_queue"]["queue_size"] > 0
        assert exporter["retry_on_failure"]["enabled"] is True
        assert exporter["retry_on_failure"]["max_elapsed_time"] == "300s"


def test_collector_health_check_is_enabled_on_localhost() -> None:
    config = load_config()

    assert config["extensions"]["health_check"]["endpoint"] == "127.0.0.1:13133"
    assert "health_check" in config["service"]["extensions"]


def test_config_has_no_debug_exporter_or_static_gcp_credentials() -> None:
    config = load_config()

    assert "debug" not in config["exporters"]
    exporter_types = {name.split("/", maxsplit=1)[0] for name in config["exporters"]}
    assert exporter_types <= {"otlp", "otlphttp"}
    assert "otlp/logs" in config["exporters"]
    assert "googleclientauth" in config["extensions"]
    assert "googleclientauth" in config["service"]["extensions"]
    for exporter in config["exporters"].values():
        assert exporter["auth"]["authenticator"] == "googleclientauth"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in CONFIG.read_text(encoding="utf-8")


def test_application_telemetry_remains_disabled_by_default() -> None:
    settings_source = (CONFIG.parents[2] / "src/config.py").read_text(encoding="utf-8")

    assert "telemetry_enabled: bool = False" in settings_source


def test_backend_startup_does_not_depend_on_collector_health() -> None:
    bootstrap = (CONFIG.parents[2] / "scripts/gcp-bootstrap.ps1").read_text(encoding="utf-8")

    assert '"--container=app"' in bootstrap
    assert '"--container=otel-collector"' in bootstrap
    assert "--depends-on=otel-collector" not in bootstrap
    assert "--startup-probe=httpGet.port=13133" not in bootstrap
    assert "OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318" in bootstrap

\n