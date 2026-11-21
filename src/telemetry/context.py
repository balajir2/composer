"""Request-scoped correlation identifiers."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

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
        request_id = uuid4().hex
        scope.setdefault("state", {})["telemetry_request_id"] = request_id
        span = None
        stack = ExitStack()
        with bind_context(request_id=request_id):
            if scope.get("path") != "/health":
                with suppress(Exception):
                    provider = self.runtime.tracer_provider
                    if provider is not None:
                        headers = {
                            k.decode("latin1").lower(): v.decode("latin1")
                            for k, v in scope["headers"]
                            if k.lower() in (b"traceparent", b"tracestate")
                        }
                        parent = TraceContextTextMapPropagator().extract(headers)
                        tracer = provider.get_tracer("composer.http")
                        span = stack.enter_context(
                            tracer.start_as_current_span(
                                scope.get("method", "HTTP"),
                                context=parent,
                                kind=SpanKind.SERVER,
                                record_exception=False,
                                set_status_on_exception=False,
                            )
                        )
                        span.set_attribute("request_id", request_id)

            async def send_response(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() != b"x-request-id"
                    ] + [(b"x-request-id", request_id.encode("ascii"))]
                    if span is not None:
                        with suppress(Exception):
                            status = message["status"]
                            span.set_attribute("http.response.status_code", status)
                            if status >= 500:
                                span.set_status(Status(StatusCode.ERROR))
                await send(message)

            try:
                await self.app(scope, receive, send_response)
            except BaseException:
                if span is not None:
                    with suppress(Exception):
                        span.set_attribute("http.response.status_code", 500)
                        span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                with suppress(Exception):
                    stack.close()
