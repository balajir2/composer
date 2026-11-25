"""Unit tests for WebSocket event streaming (Phase 9a)."""

import asyncio
import contextlib
import json
import time
from collections.abc import Generator, Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket as FastAPIWebSocket
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from starlette.websockets import WebSocketDisconnect

from src.db.models import UserRole
from src.db.repos import events as events_repo
from src.db.repos import executions as executions_repo
from src.db.repos import users as users_repo
from src.engine.events_pg import PostgresEventStore
from src.main import create_app
from src.security.jwt import create_access_token, create_refresh_token
from src.security.rate_limit import RateLimiter
from src.telemetry.providers import configure_provider_metrics
from tests.unit.db_fakes import FakeSessionmaker, RecordingSessionmaker, make_user


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
    base["user_id"] = base["userId"]  # the repository record is snake_case
    return SimpleNamespace(**base)


_ROLE = {"value": UserRole.MEMBER}
_EXECUTION: dict[str, Any] = {"value": None}


@pytest.fixture
def websocket_metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    configure_provider_metrics(provider)
    yield reader
    configure_provider_metrics(None)
    provider.shutdown()


@pytest.fixture(autouse=True)
def _fake_users(monkeypatch: pytest.MonkeyPatch) -> None:
    """The WS handler looks the caller up through the users repository."""
    _ROLE["value"] = UserRole.MEMBER

    async def _get_by_id(_session: object, user_id: str) -> users_repo.UserRecord:
        return make_user(id=user_id, role=_ROLE["value"])

    monkeypatch.setattr(users_repo, "get_by_id", _get_by_id)


@pytest.fixture(autouse=True)
def _fake_execution_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """The WS handler reads the execution through the executions repository."""
    _EXECUTION["value"] = None

    async def _get(_session: object, _execution_id: str) -> Any:
        return _EXECUTION["value"]

    monkeypatch.setattr(executions_repo, "get", _get)
    # No persisted events by default.
    monkeypatch.setattr(events_repo, "list_since", AsyncMock(return_value=[]))


class _FakeListener:
    """Stands in for ``ExecutionEventListener``: records subscriptions, hands out events."""

    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.active = 0
        self.events: list[asyncio.Event] = []
        self.loop: asyncio.AbstractEventLoop | None = None

    def wake(self) -> None:
        """Set the first subscription's event from the test thread (what a NOTIFY does)."""
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.events[0].set)

    @contextlib.contextmanager
    def subscribe(self, execution_id: str) -> Generator[asyncio.Event, None, None]:
        event = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.subscribed.append(execution_id)
        self.events.append(event)
        self.active += 1
        try:
            yield event
        finally:
            self.active -= 1


def _build_app(execution: SimpleNamespace, user_role: str = "member") -> tuple[TestClient, Any]:
    app = create_app()
    _EXECUTION["value"] = execution
    _ROLE["value"] = UserRole(user_role)
    app.state.db = MagicMock()
    app.state.db_sessionmaker = FakeSessionmaker()
    app.state.checkpointer = MagicMock()
    # Production wiring: app.state.event_bus is a PostgresEventStore over the sessionmaker.
    app.state.event_bus = PostgresEventStore(app.state.db_sessionmaker)  # pyright: ignore[reportArgumentType]
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
    client, _ = _build_app(execution, user_role="admin")
    # admin is not the owner, but role check bypasses
    token = _token_for("admin-u")
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


async def test_ws_replays_events_since_client_cursor(
    monkeypatch: pytest.MonkeyPatch,
    websocket_metric_reader: InMemoryMetricReader,
) -> None:
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

    # Replay can reach the live-listener setup before TestClient closes.
    # Keep this cursor-replay unit test independent of a real Postgres socket.
    app.state.event_listener = _FakeListener()

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
    metric_data = websocket_metric_reader.get_metrics_data()
    assert metric_data is not None
    metrics = [
        metric
        for resource_metrics in metric_data.resource_metrics
        for scope in resource_metrics.scope_metrics
        for metric in scope.metrics
    ]
    assert {metric.name for metric in metrics} >= {
        "composer.websocket.connection.duration",
        "composer.websocket.reconnect",
        "composer.websocket.replay.count",
    }
    assert all(not point.attributes for metric in metrics for point in metric.data.data_points)
    replay = next(metric for metric in metrics if metric.name == "composer.websocket.replay.count")
    replay_point = cast("Any", next(iter(replay.data.data_points)))
    assert replay_point.sum == 1
    reconnect = next(metric for metric in metrics if metric.name == "composer.websocket.reconnect")
    assert cast("Any", next(iter(reconnect.data.data_points))).value == 1


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

    app.state.event_listener = _FakeListener()

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


async def test_ws_keepalive_send_failure_closes_loop(
    monkeypatch: pytest.MonkeyPatch,
    websocket_metric_reader: InMemoryMetricReader,
) -> None:
    """If sending the keepalive ping fails (dead client that never sent a
    clean close), the live loop must give up rather than swallow the error
    and hold the WebSocket + its listener subscription open
    forever (P1-4 code review, Important #3)."""
    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)

    store = MagicMock()
    store.list_since = AsyncMock(return_value=[])
    app.state.event_bus = store

    listener = _FakeListener()
    app.state.event_listener = listener

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

    assert listener.subscribed == ["exec-1"]
    assert listener.active == 0  # the subscription was released when the loop gave up
    metric_data = websocket_metric_reader.get_metrics_data()
    assert metric_data is not None
    metrics = [
        metric
        for resource_metrics in metric_data.resource_metrics
        for scope in resource_metrics.scope_metrics
        for metric in scope.metrics
    ]
    send_failure = next(
        metric for metric in metrics if metric.name == "composer.websocket.send.failure"
    )
    assert cast("Any", next(iter(send_failure.data.data_points))).value == 1


def test_ws_closes_4500_without_a_sessionmaker() -> None:
    """No database (e.g. during shutdown) must not let a valid JWT through."""
    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)
    app.state.db_sessionmaker = None
    token = _token_for("u1")
    with (
        pytest.raises(WebSocketDisconnect) as info,
        client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]),
    ):
        pass
    assert info.value.code == 4500


def test_ws_holds_no_session_during_the_wait_or_a_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short session per read: none is open while the socket waits or sends."""
    from src.engine.events import ExecutionEvent

    events: list[str] = []
    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)
    app.state.db_sessionmaker = RecordingSessionmaker(events)

    terminal = ExecutionEvent(
        type="workflow_completed", execution_id="exec-1", seq=1, payload={"status": "completed"}
    )

    class _Store:
        calls = 0

        async def list_since(self, execution_id: str, *, after_seq: int) -> list[Any]:
            events.append("store:list")
            _Store.calls += 1
            return [] if _Store.calls == 1 else [terminal]

    app.state.event_bus = _Store()

    app.state.event_listener = _FakeListener()

    async def _wait(coro: Any, *_a: Any, **_k: Any) -> None:
        coro.close()
        events.append("ws:wait")
        raise TimeoutError

    monkeypatch.setattr("src.api.events_ws.asyncio.wait_for", _wait)

    real_send = FastAPIWebSocket.send_text

    async def _send(self: Any, text: str) -> None:
        events.append("ws:send")
        await real_send(self, text)

    monkeypatch.setattr(FastAPIWebSocket, "send_text", _send)

    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        json.loads(ws.receive_text())
        json.loads(ws.receive_text())

    assert "ws:wait" in events and "ws:send" in events
    open_sessions = 0
    for event in events:
        if event == "session:enter":
            open_sessions += 1
        elif event == "session:exit":
            open_sessions -= 1
        elif event.startswith("ws:"):
            assert open_sessions == 0, f"{event} ran inside an open session: {events}"
    assert open_sessions == 0


def test_ws_subscribes_to_its_own_execution_and_releases_on_close() -> None:
    from src.engine.events import ExecutionEvent

    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)
    terminal = ExecutionEvent(
        type="workflow_completed", execution_id="exec-1", seq=1, payload={"status": "completed"}
    )
    store = MagicMock()
    # replay: nothing; first live poll (after the listener wakes the loop): the terminal event.
    store.list_since = AsyncMock(side_effect=[[], [terminal]])
    app.state.event_bus = store
    listener = _FakeListener()
    app.state.event_listener = listener

    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        assert json.loads(ws.receive_text())["type"] == "workflow_started"
        for _ in range(300):
            if listener.events:
                break
            time.sleep(0.01)
        assert listener.subscribed == ["exec-1"]
        # Waking the subscription (what a NOTIFY for exec-1 does) triggers a prompt poll.
        listener.wake()
        assert json.loads(ws.receive_text())["type"] == "workflow_completed"

    assert listener.active == 0
