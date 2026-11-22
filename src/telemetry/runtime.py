"""Isolated, opt-in OTLP providers."""

import contextlib
import ipaddress
import os
import threading
import time
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


_HEADER_ENVIRONMENT = (
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER",
    "OTEL_PYTHON_EXPORTER_OTLP_HTTP_TRACES_CREDENTIAL_PROVIDER",
    "OTEL_PYTHON_EXPORTER_OTLP_HTTP_METRICS_CREDENTIAL_PROVIDER",
)


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
        # Public SDK APIs cannot prevent transport diagnostics from logging
        # headers or echoed credentials. Fail closed before construction, whose
        # environment-header parser can also log raw values. Use an unauthenticated
        # local collector until a transport with a public secrecy contract exists.
        if settings.otel_exporter_otlp_headers or any(
            os.environ.get(name) for name in _HEADER_ENVIRONMENT
        ):
            return
        if environment == "production" and not _safe_endpoint(settings.otel_exporter_otlp_endpoint):
            return
        orphan_exporter: SpanExporter | MetricExporter | None = None
        orphan_reader: PeriodicExportingMetricReader | None = None
        orphan_processor: BatchSpanProcessor | None = None
        try:
            endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
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
            self.tracer_provider = TracerProvider(
                resource=resource, sampler=sampler, shutdown_on_exit=False
            )
            exporter = OTLPSpanExporter(
                endpoint=f"{endpoint}/v1/traces" if endpoint else None,
                headers={},
                timeout=timeout,
            )
            orphan_exporter = exporter
            orphan_processor = BatchSpanProcessor(
                _SafeSpanExporter(exporter),
                max_queue_size=2048,
                max_export_batch_size=512,
                export_timeout_millis=timeout * 1000,
            )
            orphan_exporter = None
            self.tracer_provider.add_span_processor(orphan_processor)
            orphan_processor = None
            metric_exporter = OTLPMetricExporter(
                endpoint=f"{endpoint}/v1/metrics" if endpoint else None,
                headers={},
                timeout=timeout,
            )
            orphan_exporter = metric_exporter
            reader = PeriodicExportingMetricReader(
                _SafeMetricExporter(metric_exporter), export_timeout_millis=timeout * 1000
            )
            orphan_exporter = None
            orphan_reader = reader
            self.meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[reader],
                views=[View(instrument_name="*", aggregation_cardinality_limit=2000)],
                shutdown_on_exit=False,
            )
            from src.telemetry.providers import configure_provider_metrics

            configure_provider_metrics(self.meter_provider)
            orphan_reader = None
        except Exception:
            # Run orphan cleanup under the same overall deadline as provider cleanup.
            self._shutdown_orphans(orphan_exporter, orphan_reader, orphan_processor)

    def shutdown(self) -> None:
        self._shutdown_orphans(None, None, None)

    def _shutdown_orphans(
        self,
        exporter: SpanExporter | MetricExporter | None,
        reader: PeriodicExportingMetricReader | None,
        processor: BatchSpanProcessor | None,
    ) -> None:
        tracer, meter = self.tracer_provider, self.meter_provider
        self.tracer_provider = None
        self.meter_provider = None
        from src.telemetry.providers import configure_provider_metrics

        configure_provider_metrics(None)
        self._started = False
        timeout = self.settings.telemetry_export_timeout_seconds

        deadline = time.monotonic() + timeout

        def close_provider(provider: TracerProvider | MeterProvider) -> None:
            def flush() -> None:
                with contextlib.suppress(Exception):
                    provider.force_flush(timeout_millis=timeout * 500)

            # A blocked flush must not prevent shutdown of this provider. Python
            # cannot cancel arbitrary SDK calls; daemon workers cannot hold process
            # exit open when an exporter ignores the deadline.
            flush_worker = threading.Thread(target=flush, daemon=True)
            flush_worker.start()
            flush_worker.join(max(0, min(timeout / 2, deadline - time.monotonic())))
            with contextlib.suppress(Exception):
                if meter is not None and provider is meter:
                    meter.shutdown(timeout_millis=max(1, (deadline - time.monotonic()) * 1000))
                else:
                    provider.shutdown()

        def close_reader() -> None:
            if reader is not None:
                with contextlib.suppress(Exception):
                    reader.shutdown(timeout_millis=timeout * 1000)

        def close_exporter() -> None:
            if exporter is not None:
                with contextlib.suppress(Exception):
                    exporter.shutdown()

        def close_processor() -> None:
            if processor is not None:
                with contextlib.suppress(Exception):
                    processor.shutdown()

        workers = [
            threading.Thread(target=close_provider, args=(provider,), daemon=True)
            for provider in (tracer, meter)
            if provider is not None
        ]
        if reader is not None:
            workers.append(threading.Thread(target=close_reader, daemon=True))
        if exporter is not None:
            workers.append(threading.Thread(target=close_exporter, daemon=True))
        if processor is not None:
            workers.append(threading.Thread(target=close_processor, daemon=True))
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
