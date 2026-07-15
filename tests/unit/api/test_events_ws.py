"""Unit tests for WebSocket event streaming (Phase 9a)."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket as FastAPIWebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.engine.events_pg import PostgresEventStore
from src.main import create_app
from src.security.jwt import create_access_token, create_refresh_token
from src.security.rate_limit import RateLimiter


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "exec-1",
        "workflowId": "wf-1",
        "userId": "user-1",
        "status": "running",
        "currentNodeId": None,
        "nodeResults": {},
        "variables": {},
        "input": None,
        "output": None,
        "error": None,
        "threadId": "thread-1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _build_app(execution: SimpleNamespace, user_role: str = "member") -> tuple[TestClient, Any]:
    app = create_app()
    db = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=execution)
    db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(id=execution.userId, role=SimpleNamespace(value=user_role))
    )
    # No persisted events by default — matches production wiring
    # (app.state.event_bus is a PostgresEventStore, not the old in-process
    # ExecutionEventBus) so the WS handler's replay-from-store logic has a
    # real (mocked-at-the-db-layer) list_since to call.
    db.executionevent.find_many = AsyncMock(return_value=[])
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.event_bus = PostgresEventStore(db)
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), app


def _token_for(user_id: str) -> str:
    return create_access_token(user_id=user_id)


def test_ws_accepts_owner_with_bearer_subprotocol() -> None:
    execution = _execution_row(status="running", userId="u1")
    client, _ = _build_app(execution)
    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        msg = ws.receive_text()
        data = json.loads(msg)
        assert data["type"] == "workflow_started"
        assert data["executionId"] == "exec-1"


def test_ws_closes_4401_on_missing_bearer() -> None:
    execution = _execution_row()
    client, _ = _build_app(execution)

    # Server closes with code 4401 before accept; Starlette's TestClient raises
    # WebSocketDisconnect when the server rejects without accepting.
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/executions/exec-1/ws"):
        pass


def test_ws_closes_4401_on_refresh_token() -> None:
    execution = _execution_row(userId="u1")
    client, _ = _build_app(execution)
    token = create_refresh_token("u1")
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]),
    ):
        pass


def test_ws_closes_4403_for_non_owner() -> None:
    execution = _execution_row(status="running", userId="owner-u")
    client, _ = _build_app(execution, user_role="member")
    token = _token_for("different-u")
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]),
    ):
        pass


def test_ws_admin_bypass_on_non_owned_execution() -> None:
    execution = _execution_row(status="running", userId="owner-u")
    client, app = _build_app(execution, user_role="admin")
    # admin is not the owner, but role check bypasses
    token = _token_for("admin-u")
    # Seed admin user id on the role mock
    app.state.db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="admin-u", role=SimpleNamespace(value="admin"))
    )
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        data = json.loads(ws.receive_text())
        assert data["type"] == "workflow_started"


def test_ws_terminal_snapshot_closes() -> None:
    execution = _execution_row(status="completed", userId="u1")
    client, _ = _build_app(execution)
    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        data = json.loads(ws.receive_text())
        assert data["type"] == "workflow_completed"
        # server closes after terminal snapshot


async def test_ws_replays_events_since_client_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client reconnecting with ?after=1 must receive events with seq > 1
    from the persisted store before subscribing to new live notifications —
    the "recover missed terminal events" requirement (P1-4)."""
    from src.engine.events import ExecutionEvent

    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)

    missed_event = ExecutionEvent(
        type="node_completed", execution_id="exec-1", seq=2, payload={"nodeId": "n1"}
    )
    store = MagicMock()
    store.list_since = AsyncMock(return_value=[missed_event])
    app.state.event_bus = store

    token = _token_for("u1")
    with client.websocket_connect(
        "/executions/exec-1/ws?after=1", subprotocols=["bearer", token]
    ) as ws:
        # First message: the existing terminal/started snapshot.
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "workflow_started"
        # Second message: the replayed missed event, from list_since, not
        # from a live subscription (there is no live event queued here).
        # ExecutionEvent.as_json() flattens payload into the top-level
        # dict (see src/engine/events.py) — there is no nested "payload"
        # key on the wire.
        replayed = json.loads(ws.receive_text())
        assert replayed["type"] == "node_completed"
        assert replayed["seq"] == 2
        assert replayed["nodeId"] == "n1"

    store.list_since.assert_awaited_once_with("exec-1", after_seq=1)


def test_ws_rejects_negative_after_query_param() -> None:
    """`after` is a reconnect cursor compared against a Postgres int4 seq
    column — a negative (or otherwise invalid) value must be rejected by
    FastAPI's own validation rather than reaching list_since (P1-4 code
    review, Important #2)."""
    execution = _execution_row(status="running", userId="u1")
    client, _ = _build_app(execution)
    token = _token_for("u1")
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/executions/exec-1/ws?after=-1", subprotocols=["bearer", token]),
    ):
        pass
    # There's no HTTP response on a WS handshake failure — FastAPI/Starlette
    # surfaces a query-validation failure as a policy-violation close before
    # accept(), not a 422 response body.
    assert exc_info.value.code == 1008


async def test_ws_timeout_triggers_list_since_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TimeoutError on the 15s notified.wait() must still poll list_since
    — not just send a keepalive and skip the query. events_notify.py's
    module docstring promises a missed NOTIFY is caught up on "the next
    notification or keepalive poll"; that fallback must actually run
    (P1-4 code review, Critical)."""
    from src.engine.events import ExecutionEvent

    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)

    terminal_event = ExecutionEvent(
        type="workflow_completed", execution_id="exec-1", seq=1, payload={"status": "completed"}
    )
    store = MagicMock()
    # 1st call: step-5 replay (nothing missed). 2nd call: the live-loop poll
    # that must fire off the back of the (mocked) TimeoutError.
    store.list_since = AsyncMock(side_effect=[[], [terminal_event]])
    app.state.event_bus = store

    fake_conn = MagicMock()
    fake_conn.add_listener = AsyncMock()
    fake_conn.remove_listener = AsyncMock()
    fake_conn.close = AsyncMock()
    monkeypatch.setattr("src.api.events_ws.asyncpg.connect", AsyncMock(return_value=fake_conn))

    async def _raise_timeout(coro: Any, *_args: Any, **_kwargs: Any) -> None:
        # Close (don't await) the passed-in `notified.wait()` coroutine to
        # avoid a "coroutine was never awaited" warning — we're simulating
        # the wait timing out, not actually waiting on it.
        coro.close()
        raise TimeoutError

    monkeypatch.setattr("src.api.events_ws.asyncio.wait_for", _raise_timeout)

    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "workflow_started"
        completed = json.loads(ws.receive_text())
        assert completed["type"] == "workflow_completed"
        assert completed["status"] == "completed"

    assert store.list_since.await_count == 2
    store.list_since.assert_any_await("exec-1", after_seq=0)


async def test_ws_keepalive_send_failure_closes_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """If sending the keepalive ping fails (dead client that never sent a
    clean close), the live loop must give up rather than swallow the error
    and hold the WebSocket + its dedicated Postgres LISTEN connection open
    forever (P1-4 code review, Important #3)."""
    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)

    store = MagicMock()
    store.list_since = AsyncMock(return_value=[])
    app.state.event_bus = store

    fake_conn = MagicMock()
    fake_conn.add_listener = AsyncMock()
    fake_conn.remove_listener = AsyncMock()
    fake_conn.close = AsyncMock()
    monkeypatch.setattr("src.api.events_ws.asyncpg.connect", AsyncMock(return_value=fake_conn))

    async def _raise_timeout(coro: Any, *_args: Any, **_kwargs: Any) -> None:
        coro.close()
        raise TimeoutError

    monkeypatch.setattr("src.api.events_ws.asyncio.wait_for", _raise_timeout)
    monkeypatch.setattr(
        FastAPIWebSocket, "send_json", AsyncMock(side_effect=RuntimeError("client gone"))
    )

    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "workflow_started"
        # The keepalive send raised inside the handler, so it returns
        # (closing the socket) instead of looping forever — the client
        # sees the connection go away rather than further messages.
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()

    fake_conn.close.assert_awaited()
