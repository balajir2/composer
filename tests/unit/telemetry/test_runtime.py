from unittest.mock import MagicMock

import pytest
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


def test_real_exporter_sends_without_configured_headers(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    import requests

    response = requests.Response()
    response.status_code = 200
    response._content = b""
    send = MagicMock(return_value=response)
    monkeypatch.setattr(requests.Session, "send", send)
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
    assert runtime.meter_provider is not None
    runtime.meter_provider.get_meter("test").create_counter("count").add(1)
    runtime.shutdown()
    assert "header-secret" not in caplog.text
    assert {call.args[0].url for call in send.call_args_list} == {
        "http://localhost:4318/v1/traces",
        "http://localhost:4318/v1/metrics",
    }


def test_trace_provider_does_not_register_exit_handler(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", MagicMock())
    monkeypatch.setattr("src.telemetry.runtime.OTLPMetricExporter", MagicMock())
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318")
    )
    runtime.start("composer", "1", "test")
    assert runtime.tracer_provider is not None
    assert vars(runtime.tracer_provider).get("_atexit_handler") is None
    assert runtime.meter_provider is not None
    assert vars(runtime.meter_provider).get("_atexit_handler") is None
    runtime.shutdown()


def test_shutdown_calls_provider_shutdown_even_if_flush_fails() -> None:
    runtime = TelemetryRuntime(Settings())
    provider = MagicMock()
    provider.force_flush.side_effect = RuntimeError("failure")
    runtime.tracer_provider = provider
    runtime.shutdown()
    provider.shutdown.assert_called_once()


def test_failed_metric_reader_construction_closes_metric_exporter(monkeypatch: MonkeyPatch) -> None:
    span, metric = MagicMock(), MagicMock()
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", MagicMock(return_value=span))
    monkeypatch.setattr("src.telemetry.runtime.OTLPMetricExporter", MagicMock(return_value=metric))
    monkeypatch.setattr(
        "src.telemetry.runtime.PeriodicExportingMetricReader",
        MagicMock(side_effect=RuntimeError("failure")),
    )
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318")
    )
    runtime.start("composer", "1", "test")
    span.shutdown.assert_called_once()
    metric.shutdown.assert_called_once()


def test_blocking_provider_does_not_prevent_other_cleanup() -> None:
    import threading

    blocker = threading.Event()
    tracer, meter = MagicMock(), MagicMock()

    def blocked(**kwargs: object) -> bool:
        return blocker.wait(4)

    tracer.force_flush.side_effect = blocked
    runtime = TelemetryRuntime(Settings(telemetry_export_timeout_seconds=1))
    runtime.tracer_provider, runtime.meter_provider = tracer, meter
    try:
        runtime.shutdown()
        meter.shutdown.assert_called_once()
    finally:
        blocker.set()


def test_failed_meter_provider_construction_closes_reader(monkeypatch: MonkeyPatch) -> None:
    reader = MagicMock()
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", MagicMock())
    monkeypatch.setattr("src.telemetry.runtime.OTLPMetricExporter", MagicMock())
    monkeypatch.setattr(
        "src.telemetry.runtime.PeriodicExportingMetricReader", MagicMock(return_value=reader)
    )
    monkeypatch.setattr(
        "src.telemetry.runtime.MeterProvider", MagicMock(side_effect=RuntimeError("failed"))
    )
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318")
    )
    runtime.start("composer", "1", "test")
    reader.shutdown.assert_called_once()


def test_failed_span_processor_registration_closes_processor(monkeypatch: MonkeyPatch) -> None:
    provider, processor = MagicMock(), MagicMock()
    provider.add_span_processor.side_effect = RuntimeError("failure")
    monkeypatch.setattr("src.telemetry.runtime.TracerProvider", MagicMock(return_value=provider))
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", MagicMock())
    monkeypatch.setattr(
        "src.telemetry.runtime.BatchSpanProcessor", MagicMock(return_value=processor)
    )
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318")
    )
    runtime.start("composer", "1", "test")
    processor.shutdown.assert_called_once()


def test_invalid_environment_headers_are_rejected_without_logging(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "bad;header=secret-value")
    exporter = MagicMock()
    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", exporter)
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318")
    )
    runtime.start("composer", "1", "test")
    exporter.assert_not_called()
    assert "secret-value" not in caplog.text


@pytest.mark.parametrize(
    "source",
    [
        "settings",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_TRACES_CREDENTIAL_PROVIDER",
        "OTEL_PYTHON_EXPORTER_OTLP_HTTP_METRICS_CREDENTIAL_PROVIDER",
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        "authorization=header-secret",
        "bad;header=header-secret",
        "authorization=%20header-secret",
        "header-secret",
    ],
)
def test_configured_headers_fail_closed_before_real_exporter_construction(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture, source: str, headers: str
) -> None:
    # Keep the real exporters: rejecting only malformed syntax would leak a valid
    # credential through SDK transport diagnostics or an echoed collector response.
    import requests

    from src.telemetry import runtime as runtime_module

    span_factory = MagicMock(wraps=runtime_module.OTLPSpanExporter)
    metric_factory = MagicMock(wraps=runtime_module.OTLPMetricExporter)
    monkeypatch.setattr(runtime_module, "OTLPSpanExporter", span_factory)
    monkeypatch.setattr(runtime_module, "OTLPMetricExporter", metric_factory)
    send = MagicMock(side_effect=AssertionError("network access"))
    monkeypatch.setattr(requests.Session, "send", send)
    if source != "settings":
        monkeypatch.setenv(source, headers)
    runtime = TelemetryRuntime(
        Settings(
            telemetry_enabled=True,
            otel_exporter_otlp_endpoint="http://localhost:4318",
            otel_exporter_otlp_headers=headers if source == "settings" else "",
        )
    )
    runtime.start("composer", "1", "test")
    try:
        assert runtime.tracer_provider is None
        assert runtime.meter_provider is None
        span_factory.assert_not_called()
        metric_factory.assert_not_called()
        send.assert_not_called()
        assert "header-secret" not in caplog.text
    finally:
        runtime.shutdown()


def test_shutdown_is_attempted_even_when_same_provider_flush_blocks() -> None:
    import threading

    release = threading.Event()
    stopped = threading.Event()
    provider = MagicMock()

    def blocked_flush(**kwargs: object) -> bool:
        return release.wait(5)

    provider.force_flush.side_effect = blocked_flush
    provider.shutdown.side_effect = stopped.set
    runtime = TelemetryRuntime(Settings(telemetry_export_timeout_seconds=1))
    runtime.tracer_provider = provider
    try:
        runtime.shutdown()
        assert stopped.is_set()
    finally:
        release.set()


@pytest.mark.parametrize("block", ["export", "shutdown"])
def test_real_sdk_process_exits_after_bounded_shutdown(block: str) -> None:
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import threading
        import time
        from unittest.mock import patch
        from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
        from src.config import Settings
        from src.telemetry.runtime import TelemetryRuntime

        class StuckExporter(SpanExporter):
            def export(self, spans):
                if BLOCK == "export":
                    threading.Event().wait()
                return SpanExportResult.SUCCESS
            def shutdown(self):
                if BLOCK == "shutdown":
                    threading.Event().wait()

        with patch("requests.Session.send", side_effect=AssertionError("network access")), \
             patch("src.telemetry.runtime.OTLPSpanExporter", return_value=StuckExporter()):
            runtime = TelemetryRuntime(Settings(
                telemetry_enabled=True,
                otel_exporter_otlp_endpoint="http://localhost:4318",
                telemetry_export_timeout_seconds=1,
                otel_traces_sampler_arg=1.0,
            ))
            runtime.start("composer", "1", "test")
            assert runtime.tracer_provider is not None
            assert runtime.meter_provider is not None
            with runtime.tracer_provider.get_tracer("test").start_as_current_span("span"):
                pass
            started = time.monotonic()
            runtime.shutdown()
            assert time.monotonic() - started < 2
            print("shutdown returned", flush=True)
    """)
    # A process boundary detects SDK atexit handlers and non-daemon workers.
    result = subprocess.run(
        [sys.executable, "-c", f"BLOCK = {block!r}\n" + script],
        capture_output=True,
        text=True,
        timeout=12,
        check=True,
    )
    assert "shutdown returned" in result.stdout


@pytest.mark.parametrize("failure", ["span_processor", "metric_reader", "meter_provider"])
def test_partial_startup_releases_real_sdk_ownership_once(
    monkeypatch: MonkeyPatch, failure: str
) -> None:
    from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    class Spans(InMemorySpanExporter):
        def __init__(self) -> None:
            super().__init__()
            self.closes = 0

        def shutdown(self) -> None:
            self.closes += 1
            super().shutdown()

    class Metrics(MetricExporter):
        def __init__(self) -> None:
            super().__init__()
            self.closes = 0

        def export(
            self, metrics_data: MetricsData, timeout_millis: float = 10000, **kwargs: object
        ) -> MetricExportResult:
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis: float = 10000) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 30000, **kwargs: object) -> None:
            self.closes += 1

    spans, metrics = Spans(), Metrics()

    def span_factory(**kwargs: object) -> Spans:
        return spans

    def metric_factory(**kwargs: object) -> Metrics:
        return metrics

    monkeypatch.setattr("src.telemetry.runtime.OTLPSpanExporter", span_factory)
    monkeypatch.setattr("src.telemetry.runtime.OTLPMetricExporter", metric_factory)
    target = {
        "span_processor": "BatchSpanProcessor",
        "metric_reader": "PeriodicExportingMetricReader",
        "meter_provider": "MeterProvider",
    }[failure]
    monkeypatch.setattr(
        f"src.telemetry.runtime.{target}", MagicMock(side_effect=RuntimeError("fail"))
    )
    runtime = TelemetryRuntime(
        Settings(telemetry_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318")
    )
    runtime.start("composer", "1", "test")
    runtime.shutdown()
    assert runtime.tracer_provider is None
    assert runtime.meter_provider is None
    assert spans.closes == 1
    assert metrics.closes == (0 if failure == "span_processor" else 1)
