# Test doubles intentionally implement only the ASGI/runtime surface exercised here.
# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportAttributeAccessIssue=false
import asyncio
from typing import TYPE_CHECKING

import pytest

from src.telemetry.context import bind_context, current_correlation

if TYPE_CHECKING:
    from src.telemetry.runtime import TelemetryRuntime


def test_context_nested_reset_and_rejects_unknown_fields() -> None:
    with bind_context(request_id="outer"):
        with bind_context(request_id="inner", execution_id="e"):
            assert current_correlation().request_id == "inner"
        assert current_correlation().request_id == "outer"
        assert current_correlation().execution_id is None
    assert current_correlation().request_id is None
    with pytest.raises((TypeError, ValueError)), bind_context(payload="secret"):
        pass


@pytest.mark.asyncio
async def test_context_isolated_across_tasks_and_cleaned_on_cancel() -> None:
    async def worker(value: str) -> str | None:
        with bind_context(request_id=value):
            await asyncio.sleep(0)
            return current_correlation().request_id

    assert await asyncio.gather(worker("a"), worker("b")) == ["a", "b"]
    assert current_correlation().request_id is None

    async def cancelled() -> None:
        with bind_context(request_id="cancelled"):
            await asyncio.sleep(100)

    task = asyncio.create_task(cancelled())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert current_correlation().request_id is None


@pytest.mark.asyncio
async def test_server_error_snapshot_does_not_leak_into_another_task() -> None:
    import json
    import logging
    from types import SimpleNamespace

    from src.telemetry.context import CorrelationMiddleware
    from src.telemetry.logging import JSONFormatter

    error = RuntimeError("payload secret")

    async def app(scope, receive, send):
        raise error

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    middleware = CorrelationMiddleware(app, runtime=SimpleNamespace(tracer_provider=None))

    async def first_request():
        with pytest.raises(RuntimeError):
            await middleware(
                {"type": "http", "path": "/", "method": "GET", "headers": []}, receive, send
            )
        assert current_correlation().request_id is None

    await asyncio.create_task(first_request())
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        "",
        0,
        "Exception in ASGI application",
        (),
        (RuntimeError, error, None),
    )
    assert "request_id" not in json.loads(JSONFormatter().format(record))


@pytest.mark.asyncio
async def test_dead_snapshot_is_not_used_by_event_loop_callback() -> None:
    import json
    import logging
    import weakref

    from src.telemetry.context import CorrelationContext, _exception_marker, _request_generation
    from src.telemetry.logging import JSONFormatter

    class Token:
        pass

    token = Token()
    reference = weakref.ref(token)
    del token
    error = RuntimeError("payload")
    generation = object()
    generation_token = _request_generation.set(generation)
    error._composer_correlation = (
        _exception_marker,
        reference,
        generation,
        CorrelationContext(request_id="old"),
    )
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        "",
        0,
        "Exception in ASGI application",
        (),
        (RuntimeError, error, None),
    )
    result = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(
        lambda: result.set_result(json.loads(JSONFormatter().format(record)))
    )
    try:
        assert "request_id" not in await result
    finally:
        _request_generation.reset(generation_token)


@pytest.mark.asyncio
async def test_same_task_new_request_invalidates_old_exception_snapshot() -> None:
    from types import SimpleNamespace

    from src.telemetry.context import CorrelationMiddleware, exception_correlation

    error = RuntimeError("payload")
    observed = []

    async def failing(scope, receive, send):
        raise error

    async def later(scope, receive, send):
        observed.append(exception_correlation(error))

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    runtime = SimpleNamespace(tracer_provider=None)
    scope = {"type": "http", "path": "/", "method": "GET", "headers": []}
    with pytest.raises(RuntimeError):
        await CorrelationMiddleware(failing, runtime=runtime)(scope, receive, send)
    await CorrelationMiddleware(later, runtime=runtime)(scope, receive, send)
    assert observed == [None]


@pytest.mark.asyncio
async def test_nested_same_task_request_restores_outer_error_generation() -> None:
    from types import SimpleNamespace
    from typing import cast

    from starlette.types import Message, Receive, Scope, Send

    from src.telemetry.context import CorrelationMiddleware, exception_correlation

    error = RuntimeError("outer failure")
    inner_request_ids: list[str] = []

    async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
        inner_request_ids.append(scope["state"]["telemetry_request_id"])

    async def outer_app(scope: Scope, receive: Receive, send: Send) -> None:
        await CorrelationMiddleware(inner_app, runtime=runtime)(
            {"type": "http", "path": "/inner", "method": "GET", "headers": []},
            receive,
            send,
        )
        raise error

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        pass

    runtime = cast("TelemetryRuntime", SimpleNamespace(tracer_provider=None))
    outer_scope: Scope = {
        "type": "http",
        "path": "/outer",
        "method": "GET",
        "headers": [],
    }
    with pytest.raises(RuntimeError) as caught:
        await CorrelationMiddleware(outer_app, runtime=runtime)(outer_scope, receive, send)

    snapshot = exception_correlation(caught.value)
    assert snapshot is not None
    assert snapshot.request_id == outer_scope["state"]["telemetry_request_id"]
    assert snapshot.request_id != inner_request_ids[0]
