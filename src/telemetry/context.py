"""Request-scoped correlation identifiers."""

import asyncio
import weakref
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import cast

from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.telemetry.runtime import TelemetryRuntime


@dataclass(frozen=True)
class CorrelationContext:
    trace_id: str | None = None
    span_id: str | None = None
    request_id: str | None = None
    execution_id: str | None = None
    attempt_id: str | None = None
    node_id: str | None = None


_context: ContextVar[CorrelationContext | None] = ContextVar("correlation", default=None)
_exception_marker = object()
_request_generation: ContextVar[object | None] = ContextVar(
    "telemetry_request_generation", default=None
)


def exception_correlation(exc: BaseException) -> CorrelationContext | None:
    """Recover a snapshot written at the request boundary for server error logging."""
    try:
        value = getattr(exc, "_composer_correlation", None)
        task = asyncio.current_task()
        if not isinstance(value, tuple):
            return None
        value = cast("tuple[object, ...]", value)
        if (
            task is not None
            and len(value) == 4
            and value[0] is _exception_marker
            and isinstance(value[1], weakref.ReferenceType)
            and value[1]() is task
            and value[2] is not None
            and value[2] is _request_generation.get()
            and isinstance(value[3], CorrelationContext)
        ):
            return value[3]
    except Exception:
        pass
    return None


@contextmanager
def bind_context(**ids: str | None) -> Generator[None, None, None]:
    allowed = {"request_id", "execution_id", "attempt_id", "node_id"}
    if not ids.keys() <= allowed or any(
        v is not None and not isinstance(v, str)  # pyright: ignore[reportUnnecessaryIsInstance]
        for v in ids.values()
    ):
        raise ValueError("Invalid correlation identifier")
    token = _context.set(replace(_context.get() or CorrelationContext(), **ids))
    try:
        yield
    finally:
        _context.reset(token)


def current_correlation() -> CorrelationContext:
    current = _context.get() or CorrelationContext()
    span = trace.get_current_span().get_span_context()
    if span.is_valid:
        return replace(current, trace_id=f"{span.trace_id:032x}", span_id=f"{span.span_id:016x}")
    return current


class CorrelationMiddleware:
    """Bind a locally generated request ID and a safe server span."""

    def __init__(self, app: ASGIApp, *, runtime: TelemetryRuntime) -> None:
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from contextlib import ExitStack, suppress
        from uuid import uuid4

        from opentelemetry.trace import SpanKind, Status, StatusCode
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Invalidate earlier exception snapshots even if a server reuses this task.
        generation = object()
        _request_generation.set(generation)
        request_id = uuid4().hex
        scope.setdefault("state", {})["telemetry_request_id"] = request_id
        span = None
        method = scope.get("method", "")
        if method not in {
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "DELETE",
            "CONNECT",
            "OPTIONS",
            "TRACE",
            "PATCH",
        }:
            method = "_OTHER"
        stack = ExitStack()
        with bind_context(request_id=request_id):
            if scope.get("path") != "/health":
                with suppress(Exception):
                    provider = self.runtime.tracer_provider
                    if provider is not None:
                        headers: dict[str, str] = {}
                        for key, value in scope["headers"]:
                            name = key.decode("latin1").lower()
                            if name == "tracestate":
                                previous = headers.get(name)
                                headers[name] = (
                                    f"{previous},{value.decode('latin1')}"
                                    if previous
                                    else value.decode("latin1")
                                )
                            elif name == "traceparent":
                                headers[name] = (
                                    value.decode("latin1") if name not in headers else ""
                                )
                        parent = TraceContextTextMapPropagator().extract(headers)
                        tracer = provider.get_tracer("composer.http")
                        span = stack.enter_context(
                            tracer.start_as_current_span(
                                method,
                                context=parent,
                                kind=SpanKind.SERVER,
                                record_exception=False,
                                set_status_on_exception=False,
                            )
                        )
                        span.set_attribute("request_id", request_id)
                        span.set_attribute("http.request.method", method)

            async def send_response(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() != b"x-request-id"
                    ] + [(b"x-request-id", request_id.encode("ascii"))]
                    await send(message)
                    if span is not None:
                        with suppress(Exception):
                            status = message["status"]
                            span.set_attribute("http.response.status_code", status)
                            if status >= 500:
                                span.set_status(Status(StatusCode.ERROR))
                else:
                    await send(message)

            try:
                await self.app(scope, receive, send_response)
            except BaseException as exc:
                with suppress(Exception):
                    correlation = current_correlation()
                    snapshot = CorrelationContext(
                        request_id=correlation.request_id,
                        trace_id=correlation.trace_id,
                        span_id=correlation.span_id,
                    )
                    task = asyncio.current_task()
                    if task is not None:
                        BaseException.__setattr__(
                            exc,
                            "_composer_correlation",
                            (_exception_marker, weakref.ref(task), generation, snapshot),
                        )
                if span is not None:
                    with suppress(Exception):
                        kind = (
                            "cancelled"
                            if isinstance(exc, asyncio.CancelledError)
                            else "internal_error"
                        )
                        span.set_attribute("error.type", kind)
                        span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                if span is not None:
                    with suppress(Exception):
                        route = scope.get("route")
                        path = getattr(route, "path_format", None)
                        if isinstance(path, str) and len(path) <= 1024:
                            span.set_attribute("http.route", path)
                            span.update_name(f"{method} {path}")
                with suppress(Exception):
                    stack.close()
