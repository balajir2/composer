from unittest.mock import MagicMock

from pytest import LogCaptureFixture, MonkeyPatch

from src.config import Settings
from src.telemetry.runtime import TelemetryRuntime


def test_disabled_does_not_create_exporters(monkeypatch: MonkeyPatch) -> None:
    factory = MagicMock(side_effect=AssertionError("exporter constructed"))
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", factory)
    runtime = TelemetryRuntime(Settings(telemetry_enabled=False))
    runtime.start("composer", "1", "production")
    runtime.shutdown()
    factory.assert_not_called()


def test_start_is_idempotent_and_shutdown_is_bounded(monkeypatch: MonkeyPatch) -> None:
    span = MagicMock()
    metric = MagicMock()
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", MagicMock(return_value=span))
    monkeypatch.setattr("src.telemetry.runtime.OTLPMetricExporter", MagicMock(return_value=metric))
    runtime = TelemetryRuntime(
        Settings(
            telemetry_enabled=True,
            otel_exporter_otlp_endpoint="http://localhost:4318",
            telemetry_export_timeout_seconds=1,
        )
    )
    runtime.start("composer", "1", "test")
    provider = runtime.tracer_provider
    runtime.start("composer", "1", "test")
    assert runtime.tracer_provider is provider
    runtime.shutdown()
    span.shutdown.assert_called_once()
    metric.shutdown.assert_called_once()


def test_setup_failure_is_isolated(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.telemetry.runtime.OTLPSpanExporter", MagicMock(side_effect=RuntimeError("secret"))
    )
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="https://example.com")
    )
    runtime.start("composer", "1", "production")
    runtime.shutdown()


def test_production_rejects_remote_plain_http(monkeypatch: MonkeyPatch) -> None:
    factory = MagicMock()
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", factory)
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="http://example.com")
    )
    runtime.start("composer", "1", "production")
    factory.assert_not_called()


def test_shutdown_returns_when_provider_blocks() -> None:
    import threading
    import time

    runtime = TelemetryRuntime(Settings(telemetry_export_timeout_seconds=1))
    blocker = threading.Event()
    provider = MagicMock()

    def wait_for_release(**kwargs: object) -> bool:
        return blocker.wait(5)

    provider.force_flush.side_effect = wait_for_release
    runtime.tracer_provider = provider
    started = time.monotonic()
    try:
        runtime.shutdown()
        assert time.monotonic() - started < 2
    finally:
        blocker.set()


def test_shutdown_continues_after_one_provider_raises() -> None:
    runtime = TelemetryRuntime(Settings())
    tracer, meter = MagicMock(), MagicMock()
    tracer.force_flush.side_effect = RuntimeError("secret")
    runtime.tracer_provider, runtime.meter_provider = tracer, meter
    runtime.shutdown()
    meter.shutdown.assert_called_once()


def test_exporter_failure_does_not_expose_secret(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    span = MagicMock()
    span.export.side_effect = RuntimeError("header-secret")
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", MagicMock(return_value=span))
    monkeypatch.setattr("src.telemetry.runtime.OTLPMetricExporter", MagicMock())
    runtime = TelemetryRuntime(
        Settings(
            telemetry_enabled=True,
            otel_exporter_otlp_endpoint="http://localhost:4318",
            otel_traces_sampler_arg=1.0,
        )
    )
    runtime.start("composer", "1", "test")
    assert runtime.tracer_provider is not None
    with runtime.tracer_provider.get_tracer("test").start_as_current_span("test"):
        pass
    runtime.shutdown()
    span.export.assert_called_once()
    assert "header-secret" not in caplog.text
