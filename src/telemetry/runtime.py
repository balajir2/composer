"""Isolated, opt-in OTLP providers."""

import contextlib
import ipaddress
import threading
from collections.abc import Sequence
from urllib.parse import urlsplit

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricsData,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from src.config import Settings, get_settings


class _SafeSpanExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            return self.exporter.export(spans)
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            self.exporter.shutdown()


class _SafeMetricExporter(MetricExporter):
    def __init__(self, exporter: MetricExporter) -> None:
        super().__init__()
        self.exporter = exporter

    def export(
        self, metrics_data: MetricsData, timeout_millis: float = 10000, **kwargs: object
    ) -> MetricExportResult:
        try:
            return self.exporter.export(metrics_data, timeout_millis=timeout_millis, **kwargs)
        except Exception:
            return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 10000) -> bool:
        try:
            return self.exporter.force_flush(timeout_millis=timeout_millis)
        except Exception:
            return False

    def shutdown(self, timeout_millis: float = 30000, **kwargs: object) -> None:
        with contextlib.suppress(Exception):
            self.exporter.shutdown(timeout_millis=timeout_millis, **kwargs)


def _safe_endpoint(endpoint: str) -> bool:
    try:
        url = urlsplit(endpoint)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            return False
        if url.scheme == "https":
            return True
        if url.hostname.lower() == "localhost":
            return True
        return ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        return False


class TelemetryRuntime:
    """Own providers without altering global instrumentation state."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.tracer_provider: TracerProvider | None = None
        self.meter_provider: MeterProvider | None = None
        self._started = False

    def start(self, app_name: str, app_version: str, environment: str) -> None:
        if self._started:
            return
        self._started = True
        settings = self.settings
        if not settings.telemetry_enabled:
            return
        if environment == "production" and not _safe_endpoint(settings.otel_exporter_otlp_endpoint):
            return
        try:
            endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
            headers = dict(
                part.split("=", 1)
                for part in settings.otel_exporter_otlp_headers.split(",")
                if "=" in part
            )
            timeout = settings.telemetry_export_timeout_seconds
            resource = Resource.create(
                {
                    "service.name": app_name,
                    "service.version": app_version,
                    "service.namespace": settings.telemetry_service_namespace,
                    "deployment.environment.name": environment,
                }
            )
            sampler = ParentBased(TraceIdRatioBased(settings.otel_traces_sampler_arg))
            if settings.otel_traces_sampler != "parentbased_traceidratio":
                return
            self.tracer_provider = TracerProvider(resource=resource, sampler=sampler)
            exporter = OTLPSpanExporter(
                endpoint=f"{endpoint}/v1/traces" if endpoint else None,
                headers=headers,
                timeout=timeout,
            )
            self.tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    _SafeSpanExporter(exporter),
                    max_queue_size=2048,
                    max_export_batch_size=512,
                    export_timeout_millis=timeout * 1000,
                )
            )
            metric_exporter = OTLPMetricExporter(
                endpoint=f"{endpoint}/v1/metrics" if endpoint else None,
                headers=headers,
                timeout=timeout,
            )
            reader = PeriodicExportingMetricReader(
                _SafeMetricExporter(metric_exporter), export_timeout_millis=timeout * 1000
            )
            self.meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[reader],
                views=[View(instrument_name="*", aggregation_cardinality_limit=2000)],
                shutdown_on_exit=False,
            )
        except Exception:
            self.shutdown()

    def shutdown(self) -> None:
        tracer, meter = self.tracer_provider, self.meter_provider
        self.tracer_provider = None
        self.meter_provider = None
        self._started = False
        timeout = self.settings.telemetry_export_timeout_seconds

        def close() -> None:
            for provider in (tracer, meter):
                if provider is None:
                    continue
                try:
                    provider.force_flush(timeout_millis=timeout * 1000)
                    provider.shutdown()
                except Exception:
                    pass

        thread = threading.Thread(target=close, daemon=True)
        thread.start()
        thread.join(timeout)
