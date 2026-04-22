"""Tests for GET /executions/{id}/events (SSE)."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "e1",
        "status": "running",
        "userId": "dev",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _build_app(monkeypatch: pytest.MonkeyPatch, execution: Any | None) -> Any:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings
    from src.engine.events import ExecutionEventBus

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=execution)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
    return app


async def test_events_stream_404_on_unknown_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/executions/ghost/events")
    assert resp.status_code == 404


async def test_events_stream_terminal_sends_snapshot_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If status is already completed, stream sends one status-change and closes."""
    app = _build_app(monkeypatch, _execution_row(status="completed"))

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
        ac.stream("GET", "/executions/e1/events") as resp,
    ):
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = b""
        async for chunk in resp.aiter_bytes():
            body += chunk
            if b"\n\n" in body:
                break

    assert b"event: status-change" in body
    data_line = next(line for line in body.decode("utf-8").split("\n") if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["type"] == "status-change"
    assert payload["payload"]["status"] == "completed"


async def test_stream_events_non_owner_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner streaming an execution's events gets 404 (info-leak tight)."""
    app = _build_app(monkeypatch, _execution_row(id="e1", status="running", userId="someone-else"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/executions/e1/events")
    assert resp.status_code == 404


async def test_events_stream_live_delivers_emitted_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscribe to a running execution, emit via the bus, assert receive."""
    from src.engine.events import ExecutionEvent

    app = _build_app(monkeypatch, _execution_row(status="running"))
    bus = app.state.event_bus

    async def _emit_later() -> None:
        await asyncio.sleep(0.2)
        await bus.emit(
            ExecutionEvent(type="node-start", execution_id="e1", payload={"node_id": "s"})
        )
        await asyncio.sleep(0.05)
        await bus.close("e1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        task = asyncio.create_task(_emit_later())
        frames: list[str] = []
        async with ac.stream("GET", "/executions/e1/events") as resp:
            assert resp.status_code == 200
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    frames.append(frame)
                if len(frames) >= 2:
                    break
        await task

    assert any("event: status-change" in f and '"running"' in f for f in frames)
    assert any("event: node-start" in f for f in frames)
