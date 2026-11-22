"""Tests for create_app()'s mode-aware behavior (Phase 7a)."""

# Tests use deliberately partial ASGI, span, and runtime doubles.
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportUnknownLambdaType=false, reportOperatorIssue=false

import pytest


def test_standalone_includes_auth_register_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    app = create_app()
    # FastAPI >=0.150 nests included routers (_IncludedRouter) in app.routes,
    # so list the served paths from the public OpenAPI schema instead.
    paths = set(app.openapi()["paths"])
    assert "/auth/register" in paths
    assert "/auth/login" in paths
    assert "/auth/refresh" in paths
    assert "/auth/disconnect" in paths
    assert "/auth/me" in paths


def test_embedded_excludes_auth_register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret-for-testing-32-chars-minimum")
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    app = create_app()
    # FastAPI >=0.150 nests included routers (_IncludedRouter) in app.routes,
    # so list the served paths from the public OpenAPI schema instead.
    paths = set(app.openapi()["paths"])
    # Standalone-only routes absent
    assert "/auth/register" not in paths
    assert "/auth/login" not in paths
    assert "/auth/refresh" not in paths
    assert "/auth/disconnect" not in paths
    # Common route stays
    assert "/auth/me" in paths


def test_embedded_missing_issuer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret-for-testing-32-chars-minimum")
    monkeypatch.delenv("IEP_JWT_ISSUER", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    with pytest.raises(RuntimeError, match="IEP_JWT_ISSUER"):
        create_app()


def test_embedded_missing_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.delenv("IEP_SHARED_SECRET", raising=False)
    monkeypatch.delenv("IEP_JWKS_URL", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    with pytest.raises(RuntimeError, match="IEP_SHARED_SECRET"):
        create_app()


def test_standalone_does_not_raise_on_missing_iep_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone mode must not require any IEP_* vars."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    for var in ["IEP_JWT_ISSUER", "IEP_SHARED_SECRET", "IEP_JWKS_URL", "IEP_UI_ORIGIN"]:
        monkeypatch.delenv(var, raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    app = create_app()  # should not raise
    assert app is not None


# ── CORS (standalone + production) ──────────────────────────────
#
# Phase 7a only plumbed an allowed origin for embedded mode.  A standalone
# production deploy with no env var ended up with allow_origins=[] (every
# preflight rejected), which silently broke the Cloud Run frontend.
# These tests pin down the new branch behaviour.


def _cors_allow_origins(app: object) -> list[str]:
    """Pull the CORSMiddleware's allow_origins back out of the constructed app."""
    from fastapi.middleware.cors import CORSMiddleware

    user_middleware = getattr(app, "user_middleware", [])
    for mw in user_middleware:
        if getattr(mw, "cls", None) is CORSMiddleware:
            kwargs = getattr(mw, "kwargs", {}) or {}
            origins = kwargs.get("allow_origins", [])
            return list(origins) if origins is not None else []
    raise AssertionError("CORSMiddleware not registered on the app")


def test_cors_standalone_development_allows_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("COMPOSER_FRONTEND_ORIGINS", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    assert _cors_allow_origins(create_app()) == ["*"]


def test_cors_standalone_production_uses_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "COMPOSER_FRONTEND_ORIGINS",
        " https://a.example.com , https://b.example.com ",
    )
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    assert _cors_allow_origins(create_app()) == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_cors_standalone_production_empty_origins_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("COMPOSER_FRONTEND_ORIGINS", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    with caplog.at_level("WARNING", logger="src.main"):
        app = create_app()
    assert _cors_allow_origins(app) == []
    assert any("COMPOSER_FRONTEND_ORIGINS" in r.message for r in caplog.records), (
        "expected a startup warning about empty CORS allowlist"
    )


def test_cors_embedded_uses_iep_ui_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret-for-testing-32-chars-minimum")
    monkeypatch.setenv("IEP_UI_ORIGIN", "https://iep.example.com")
    # Even if a standalone-style env var is set, embedded mode must ignore it.
    monkeypatch.setenv("COMPOSER_FRONTEND_ORIGINS", "https://leaked.example.com")
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    assert _cors_allow_origins(create_app()) == ["https://iep.example.com"]


def test_http_correlation_ignores_untrusted_request_identity() -> None:
    from fastapi import Request
    from fastapi.testclient import TestClient

    from src.main import create_app
    from src.telemetry.context import current_correlation

    app = create_app()

    @app.get("/correlation-test")
    async def correlation_test(request: Request) -> dict[str, str | None]:
        return {"request_id": current_correlation().request_id, "client": request.client.host}

    response = TestClient(app).get(
        "/correlation-test",
        headers={"x-request-id": "untrusted", "x-forwarded-for": "192.0.2.1"},
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.headers["x-request-id"] != "untrusted"
    assert len(response.headers["x-request-id"]) == 32
    assert current_correlation().request_id is None


def test_tracing_propagation_status_and_health_exclusion() -> None:
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    from src.main import create_app
    from src.telemetry.context import current_correlation

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app()
    app.state.telemetry_runtime.tracer_provider = provider

    @app.get("/trace-test")
    async def trace_test() -> dict[str, str | None]:
        c = current_correlation()
        return {"trace_id": c.trace_id, "span_id": c.span_id, "request_id": c.request_id}

    client = TestClient(app)
    trace_id = "0123456789abcdef0123456789abcdef"
    response = client.get(
        "/trace-test",
        headers={
            "traceparent": f"00-{trace_id}-0123456789abcdef-01",
            "tracestate": "vendor=opaque",
        },
    )
    assert response.json()["trace_id"] == trace_id
    assert response.json()["request_id"] == response.headers["x-request-id"]
    span = exporter.get_finished_spans()[-1]
    assert span.attributes["request_id"] == response.headers["x-request-id"]
    assert f"{span.parent.span_id:016x}" == "0123456789abcdef"
    assert span.context.trace_state.get("vendor") == "opaque"
    client.get("/trace-test", headers={"traceparent": "malformed"})
    assert exporter.get_finished_spans()[-1].parent is None
    before = len(exporter.get_finished_spans())
    client.get("/health")
    assert len(exporter.get_finished_spans()) == before
    client.get("/missing")
    assert exporter.get_finished_spans()[-1].status.status_code == StatusCode.UNSET
    assert exporter.get_finished_spans()[-1].attributes["http.response.status_code"] == 404
    provider.shutdown()


def test_telemetry_end_failure_does_not_change_response() -> None:
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    from src.main import create_app

    class BrokenTracer:
        @contextmanager
        def start_as_current_span(self, *args: object, **kwargs: object):
            yield None
            raise RuntimeError("export failure")

    class BrokenProvider:
        def get_tracer(self, name: str) -> BrokenTracer:
            return BrokenTracer()

    app = create_app()
    app.state.telemetry_runtime.tracer_provider = BrokenProvider()
    assert TestClient(app).get("/missing").status_code == 404


@pytest.mark.asyncio
async def test_telemetry_wraps_both_database_lifecycles_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    import src.main as main

    events: list[str] = []

    class Runtime:
        def start(self, *args: object) -> None:
            events.append("telemetry start")
            raise RuntimeError("failure")

        def shutdown(self) -> None:
            events.append("telemetry stop")
            raise RuntimeError("failure")

    @asynccontextmanager
    async def prisma(app: object):
        events.append("prisma start")
        try:
            yield object()
        finally:
            events.append("prisma stop")

    @asynccontextmanager
    async def sqlalchemy(app: object):
        events.append("sql start")
        try:
            yield
        finally:
            events.append("sql stop")

    async def sync(db: object) -> None:
        events.append("sync")

    async def stop(task: object) -> None:
        events.append("sweeper stop")

    monkeypatch.setattr(main, "prisma_lifespan", prisma)
    monkeypatch.setattr(main, "sqlalchemy_lifespan", sqlalchemy)
    monkeypatch.setattr(main, "sync_llm_keys_from_db", sync)
    monkeypatch.setattr(
        main, "start_sweeper", lambda *args, **kwargs: events.append("sweeper start")
    )
    monkeypatch.setattr(main, "stop_sweeper", stop)
    monkeypatch.setattr(main, "validate_production_config", lambda settings: None)
    app = main.create_app()
    app.state.telemetry_runtime = Runtime()

    @asynccontextmanager
    async def session():
        yield object()

    app.state.db_sessionmaker = session
    async with main.lifespan(app):
        events.append("serving")
    assert events == [
        "telemetry start",
        "prisma start",
        "sql start",
        "sync",
        "sweeper start",
        "serving",
        "sweeper stop",
        "sql stop",
        "prisma stop",
        "telemetry stop",
    ]


def test_error_response_records_error_status_without_exception_payload() -> None:
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    from src.main import create_app

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app()
    app.state.telemetry_runtime.tracer_provider = provider

    @app.get("/failure-test")
    async def failure() -> None:
        raise RuntimeError("private body contents")

    response = TestClient(app, raise_server_exceptions=False).get("/failure-test")
    assert response.status_code == 500
    assert len(response.headers["x-request-id"]) == 32
    span = exporter.get_finished_spans()[-1]
    assert span.status.status_code == StatusCode.ERROR
    assert "private body contents" not in str(span.events)
    provider.shutdown()


@pytest.mark.asyncio
async def test_concurrent_http_requests_keep_distinct_context() -> None:
    import asyncio

    import httpx

    from src.main import create_app
    from src.telemetry.context import current_correlation

    app = create_app()
    entered = 0
    ready = asyncio.Event()

    @app.get("/concurrency-test")
    async def concurrency() -> dict[str, str | None]:
        nonlocal entered
        identifier = current_correlation().request_id
        entered += 1
        if entered == 2:
            ready.set()
        await ready.wait()
        return {"before": identifier, "after": current_correlation().request_id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        one, two = await asyncio.gather(
            client.get("/concurrency-test"), client.get("/concurrency-test")
        )
    assert one.json()["before"] == one.json()["after"] == one.headers["x-request-id"]
    assert two.json()["before"] == two.json()["after"] == two.headers["x-request-id"]
    assert one.headers["x-request-id"] != two.headers["x-request-id"]
    assert current_correlation().request_id is None


def test_span_uses_route_template_method_and_repeated_tracestate() -> None:
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from src.main import create_app

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app()
    app.state.telemetry_runtime.tracer_provider = provider

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    client = TestClient(app)
    response = client.get(
        "/items/private123?secret=token",
        headers=[
            ("traceparent", "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"),
            ("tracestate", "a=one"),
            ("tracestate", "b=two"),
        ],
    )
    assert response.status_code == 200
    span = exporter.get_finished_spans()[-1]
    assert span.name == "GET /items/{item_id}"
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["http.route"] == "/items/{item_id}"
    assert span.context.trace_state.get("a") == "one"
    assert span.context.trace_state.get("b") == "two"
    assert "private123" not in str(span.attributes) + span.name
    assert "secret" not in str(span.attributes) + span.name
    provider.shutdown()


@pytest.mark.asyncio
async def test_cancellation_does_not_invent_http_status() -> None:
    import asyncio
    from types import SimpleNamespace

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from src.telemetry.context import CorrelationMiddleware

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    async def app(scope, receive, send):
        raise asyncio.CancelledError()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = CorrelationMiddleware(app, runtime=SimpleNamespace(tracer_provider=provider))
    with pytest.raises(asyncio.CancelledError):
        await middleware(
            {"type": "http", "path": "/cancel", "method": "GET", "headers": []}, receive, send
        )
    span = exporter.get_finished_spans()[-1]
    assert "http.response.status_code" not in span.attributes
    provider.shutdown()


@pytest.mark.asyncio
async def test_cancellation_after_response_preserves_sent_status() -> None:
    import asyncio
    from types import SimpleNamespace

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from src.telemetry.context import CorrelationMiddleware

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 202, "headers": []})
        raise asyncio.CancelledError()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = CorrelationMiddleware(app, runtime=SimpleNamespace(tracer_provider=provider))
    with pytest.raises(asyncio.CancelledError):
        await middleware(
            {"type": "http", "path": "/cancel", "method": "GET", "headers": []}, receive, send
        )
    span = exporter.get_finished_spans()[-1]
    assert span.attributes["http.response.status_code"] == 202
    assert span.attributes["error.type"] == "cancelled"
    provider.shutdown()


@pytest.mark.asyncio
async def test_failed_response_send_does_not_record_unsent_status() -> None:
    import asyncio
    from types import SimpleNamespace

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from src.telemetry.context import CorrelationMiddleware

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 201, "headers": []})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        raise asyncio.CancelledError()

    middleware = CorrelationMiddleware(app, runtime=SimpleNamespace(tracer_provider=provider))
    with pytest.raises(asyncio.CancelledError):
        await middleware(
            {"type": "http", "path": "/cancel", "method": "GET", "headers": []}, receive, send
        )
    assert "http.response.status_code" not in exporter.get_finished_spans()[-1].attributes
    provider.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["access", "error", "span"])
async def test_uvicorn_h11_unhandled_error_keeps_request_correlation(signal, monkeypatch):
    import asyncio
    import io
    import json
    import logging

    import httpx
    import uvicorn
    from fastapi import Request
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from src.main import create_app
    from src.telemetry.context import current_correlation
    from src.telemetry.logging import JSONFormatter

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app()
    app.state.telemetry_runtime.tracer_provider = provider

    traceparents = []
    client_hosts = []

    @app.get("/h11-failure/{item_id}")
    async def fail(item_id: str, request: Request) -> None:
        traceparents.append(request.headers.get("traceparent"))
        client_hosts.append(request.client.host if request.client else None)
        raise RuntimeError("private body secret")

    @app.get("/h11-ok")
    async def ok() -> dict[str, str | None]:
        return {"request_id": current_correlation().request_id}

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    for name in ("uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", [handler])
        monkeypatch.setattr(logger, "propagate", False)
        monkeypatch.setattr(logger, "level", logging.INFO)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, http="h11", lifespan="off", log_config=None
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get(
                "/h11-failure/private-id?token=secret",
                headers={
                    "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
                    "x-request-id": "forged",
                    "x-forwarded-for": "9.9.9.9, 203.0.113.7",
                    "x-forwarded-proto": "https",
                },
            )
            assert response.status_code == 500
            assert traceparents == ["00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"]
            assert client_hosts == ["203.0.113.7"]
            from src.security.proxy_headers import TrustedProxyHeadersMiddleware
            from src.telemetry.context import CorrelationMiddleware

            assert isinstance(app.middleware_stack, TrustedProxyHeadersMiddleware)
            assert isinstance(app.middleware_stack.app, CorrelationMiddleware)
            request_id = response.headers["x-request-id"]
            assert request_id != "forged"
            later = await client.get("/h11-ok")
            assert later.json()["request_id"] == later.headers["x-request-id"] != request_id
        for _ in range(100):
            if (
                len(exporter.get_finished_spans()) >= 2
                and '"logger": "uvicorn.error"' in stream.getvalue()
            ):
                break
            await asyncio.sleep(0.01)
        spans = exporter.get_finished_spans()
        failed = next(
            span
            for span in spans
            if span.name == "GET /h11-failure/{item_id}"
            or span.attributes.get("request_id") == request_id
        )
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert "private body" not in stream.getvalue() and "token=secret" not in stream.getvalue()
        healthy_log = next(
            row
            for row in rows
            if row["logger"] == "uvicorn.access" and row.get("status_code") == 200
        )
        assert healthy_log["request_id"] == later.headers["x-request-id"]
        assert healthy_log["trace_id"] != "0123456789abcdef0123456789abcdef"
        if signal == "span":
            assert failed.attributes["http.response.status_code"] == 500
        else:
            selected = [
                row
                for row in rows
                if row["logger"] == f"uvicorn.{signal}"
                and (
                    (signal == "error" and row.get("level") == "ERROR")
                    or row.get("status_code") == 500
                )
            ]
            assert len(selected) == 1
            row = selected[0]
            assert row["request_id"] == request_id
            assert row["trace_id"] == "0123456789abcdef0123456789abcdef"
            assert row["span_id"] == f"{failed.context.span_id:016x}"
        assert current_correlation().request_id is None
    finally:
        server.should_exit = True
        await task
        provider.shutdown()
