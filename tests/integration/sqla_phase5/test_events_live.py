"""Live WebSocket delivery rides LISTEN/NOTIFY (well under the 15 s keepalive poll); reconnect
replays from the ``?after=`` cursor; a real run's events reach a watcher end to end.

Starlette's synchronous ``TestClient`` on its own app (as tests/integration/sqla_phase4a/
test_events_ws.py does); every DB setup runs on the app's loop through ``client.portal.call``.
A receive that would block forever is turned into a failure by ``_recv``'s deadline: every
message must arrive in seconds, so a delivery that only works through the 15 s poll fails.

Seams used: ``app.state.event_bus.append``, ``notify_execution_event(execution_id, *, seq)``, the
WebSocket route and, for the end-to-end test, the real ``enqueue_execution`` (dev-mode fallback).
"""

import json
import queue
import secrets
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from starlette.websockets import WebSocketDisconnect

from src.config import get_settings
from src.db.models import User, UserRole, Workflow, WorkflowAssignment
from src.main import create_app
from tests._rate_limit_reset import reset_rate_limit_buckets
from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    approval_graph,
    install_email_recorder,
    purge_execution_artifacts,
)
from tests.integration.sqla_phase5.conftest import purge_threads

pytestmark = [pytest.mark.integration, HTTPX_MARK]

T = TypeVar("T")
LIVE_DEADLINE = 4.0  # seconds; the keepalive poll is 15 s


@pytest.fixture(autouse=True)
def _drive_cloud_tasks_synchronously() -> None:  # pyright: ignore[reportUnusedFunction]
    """Shadow tests/integration/conftest.py's fixture of the same name: it requests the async
    ``app`` fixture, i.e. a second app on the pytest loop, which this module must not create."""
    return None


@pytest.fixture(autouse=True)
def _purge_tracked_checkpoints() -> None:  # pyright: ignore[reportUnusedFunction]
    """Shadow the package's autouse purge (it requests the async ``app`` fixture: a second app
    with a full lifespan beside this module's TestClient app). ``LiveEnv.cleanup`` purges its own
    threads instead."""
    return None


class LiveEnv:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.app: Any = client.app
        self.sm: Any = self.app.state.db_sessionmaker
        self.db = Db(self.sm)
        self.users: list[str] = []
        self.threads: list[str] = []

    def run(self, fn: Callable[..., Awaitable[T]], *args: Any) -> T:
        return self.client.portal.call(fn, *args)  # type: ignore[union-attr]

    def user(self, *, admin: bool = False) -> tuple[str, str]:
        self.run(reset_rate_limit_buckets)
        email = f"p5-ws-{secrets.token_hex(6)}@example.com"
        r = self.client.post(
            "/auth/register", json={"email": email, "password": "correct-horse-battery-staple"}
        )
        assert r.status_code == 201, r.text
        uid, token = r.json()["id"], r.json()["accessToken"]
        self.users.append(uid)
        if admin:

            async def _promote() -> None:
                async with self.sm() as s:
                    await s.execute(update(User).where(User.id == uid).values(role=UserRole.ADMIN))
                    await s.commit()

            self.run(_promote)
        return uid, token

    def running_execution(self) -> tuple[str, str]:
        uid, token = self.user()

        async def _make() -> str:
            wid = await self.db.seed_workflow(uid)
            return await self.db.seed_execution(wid, uid, status="running")

        eid = self.run(_make)
        self.threads.append(self.run(self._thread_of, eid))
        return eid, token

    async def _thread_of(self, eid: str) -> str:
        return (await self.db.must(eid)).thread_id

    def append(self, eid: str, type_: str, payload: dict[str, Any]) -> int:
        from src.engine.events import ExecutionEvent

        async def _append() -> int:
            event = ExecutionEvent(type=cast("Any", type_), execution_id=eid, payload=payload)
            return cast("int", await self.app.state.event_bus.append(event))

        return self.run(_append)

    def notify(self, eid: str, seq: int) -> None:
        from src.engine.events_notify import notify_execution_event

        async def _notify() -> None:
            await notify_execution_event(eid, seq=seq)

        self.run(_notify)

    def connect(self, eid: str, token: str | None, after: int | None = None) -> Any:
        url = f"/executions/{eid}/ws" + (f"?after={after}" if after is not None else "")
        return self.client.websocket_connect(url, subprotocols=["bearer", token] if token else None)

    def cleanup(self) -> None:
        async def _clean() -> None:
            await purge_execution_artifacts(self.sm, user_ids=self.users)
            await purge_threads(self.sm, self.threads)
            async with self.sm() as s:
                await s.execute(delete(Workflow).where(Workflow.user_id.in_(self.users)))
                await s.execute(
                    delete(WorkflowAssignment).where(WorkflowAssignment.user_id.in_(self.users))
                )
                await s.execute(delete(User).where(User.id.in_(self.users)))
                await s.commit()
            import src.engine.events_notify as events_notify

            close = getattr(events_notify, "close_notify_connection", None)
            if close is not None:  # today's dedicated asyncpg NOTIFY connection
                await close()

        self.run(_clean)


@pytest.fixture
def ws_env(phase4a_env: None) -> Iterator[LiveEnv]:
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        env = LiveEnv(c)
        try:
            yield env
        finally:
            env.cleanup()


_QUEUE_ATTR = "_p5_reader_queue"


def _reader(sock: Any) -> "queue.Queue[Any]":
    """One persistent reader thread per socket feeding a queue, so a receive that times out never
    swallows the NEXT message (a blocked ``receive_text`` keeps its place in the queue).

    The queue lives on the socket object itself, not in a dict keyed by ``id(sock)``: CPython
    reuses a freed object's id, so a later socket could otherwise pick up a dead reader's queue."""
    existing: queue.Queue[Any] | None = sock.__dict__.get(_QUEUE_ATTR)
    if existing is not None:
        return existing
    q: queue.Queue[Any] = queue.Queue()
    sock.__dict__[_QUEUE_ATTR] = q

    def _pump() -> None:
        while True:
            try:
                q.put(sock.receive_text())
            except BaseException as exc:  # disconnect or closed session ends the reader
                q.put(exc)
                return

    threading.Thread(target=_pump, daemon=True).start()
    return q


def _recv(sock: Any, timeout: float = LIVE_DEADLINE) -> dict[str, Any]:
    """One JSON message, or an AssertionError after ``timeout`` seconds. A disconnect is raised
    as the original ``WebSocketDisconnect``."""
    try:
        item = _reader(sock).get(timeout=timeout)
    except queue.Empty:
        raise AssertionError(f"no WebSocket message within {timeout}s") from None
    if isinstance(item, BaseException):
        raise item
    return cast("dict[str, Any]", json.loads(item))


def _assert_silent(sock: Any, seconds: float) -> None:
    try:
        item = _reader(sock).get(timeout=seconds)
    except queue.Empty:
        return
    raise AssertionError(f"unexpected WebSocket traffic: {item!r}"[:200])


class _Renotifier:
    """NOTIFY is only a wake-up and is safe to repeat; repeating covers the LISTEN-setup window
    after the snapshot (a notification sent before the listener exists is lost, by design)."""

    def __init__(self, env: LiveEnv, eid: str, seq: int) -> None:
        self._env, self._eid, self._seq = env, eid, seq
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._env.notify(self._eid, self._seq)
            except Exception:  # portal closed during teardown
                return
            time.sleep(0.25)

    def __enter__(self) -> "_Renotifier":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def test_live_event_arrives_within_seconds_of_its_notify(ws_env: LiveEnv) -> None:
    eid, token = ws_env.running_execution()
    with ws_env.connect(eid, token) as sock:
        assert _recv(sock)["type"] == "workflow_started"  # snapshot
        seq = ws_env.append(eid, "node_started", {"node_id": "n1"})
        started = time.monotonic()
        with _Renotifier(ws_env, eid, seq):
            msg = _recv(sock)
        assert msg["type"] == "node_started" and msg["seq"] == seq and msg["node_id"] == "n1"
        assert time.monotonic() - started < LIVE_DEADLINE


def test_every_watcher_of_an_execution_gets_each_event(ws_env: LiveEnv) -> None:
    eid, token = ws_env.running_execution()
    with ws_env.connect(eid, token) as one, ws_env.connect(eid, token) as two:
        assert _recv(one)["type"] == "workflow_started"
        assert _recv(two)["type"] == "workflow_started"
        seq = ws_env.append(eid, "node_started", {"node_id": "shared"})
        with _Renotifier(ws_env, eid, seq):
            first, second = _recv(one), _recv(two)
        assert first["seq"] == second["seq"] == seq
        assert first["node_id"] == second["node_id"] == "shared"


def test_other_executions_notifications_are_not_delivered(ws_env: LiveEnv) -> None:
    """Another execution's events never reach this watcher. (That a notification only WAKES the
    watchers of its own execution is a property of the new listener and is covered by phase 5
    Task 8's unit test; over the socket the two are indistinguishable.)"""
    mine, token = ws_env.running_execution()
    theirs, _other_token = ws_env.running_execution()
    with ws_env.connect(mine, token) as sock:
        assert _recv(sock)["executionId"] == mine
        for node_id in ("t1", "t2"):
            seq = ws_env.append(theirs, "node_started", {"node_id": node_id})
            ws_env.notify(theirs, seq)
        own = ws_env.append(mine, "node_started", {"node_id": "mine"})
        with _Renotifier(ws_env, mine, own):
            msg = _recv(sock)
        assert msg["executionId"] == mine and msg["node_id"] == "mine" and msg["seq"] == own


def test_reconnect_with_cursor_replays_only_missed_events_then_goes_live(ws_env: LiveEnv) -> None:
    eid, token = ws_env.running_execution()
    s1 = ws_env.append(eid, "node_started", {"node_id": "a"})
    s2 = ws_env.append(eid, "node_completed", {"node_id": "a"})
    with ws_env.connect(eid, token, after=s1) as sock:
        assert _recv(sock)["seq"] is None  # snapshot
        replayed = _recv(sock)
        assert replayed["seq"] == s2 and replayed["type"] == "node_completed"
        s3 = ws_env.append(eid, "node_started", {"node_id": "b"})
        with _Renotifier(ws_env, eid, s3):
            live = _recv(sock)
        assert live["seq"] == s3 and live["node_id"] == "b"


def test_a_terminal_live_event_is_delivered_then_the_socket_closes_normally(
    ws_env: LiveEnv,
) -> None:
    eid, token = ws_env.running_execution()
    with ws_env.connect(eid, token) as sock:
        _recv(sock)
        seq = ws_env.append(eid, "workflow_completed", {"status": "completed"})
        with _Renotifier(ws_env, eid, seq):
            done = _recv(sock)
        assert done["type"] == "workflow_completed" and done["seq"] == seq
        with pytest.raises(WebSocketDisconnect) as exc:
            _recv(sock)
        assert exc.value.code == 1000


def test_a_real_paused_and_resumed_run_streams_its_events_to_a_watcher(
    ws_env: LiveEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: dev-mode in-process run (real graph, real checkpointer), a watcher that
    connects right after the create, then the resume. Whether each event arrives by replay or by
    live push, the watcher sees the whole ordered sequence, gaps-free."""
    install_email_recorder(monkeypatch)
    uid, token = ws_env.user()
    nodes, edges = approval_graph()

    async def _seed() -> str:
        return await ws_env.db.seed_workflow(uid, nodes=nodes, edges=edges)

    wid = ws_env.run(_seed)
    created = ws_env.client.post(
        "/executions",
        json={"workflowId": wid, "input": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 202, created.text
    eid = created.json()["id"]

    async def _thread() -> str:
        return (await ws_env.db.must(eid)).thread_id

    ws_env.threads.append(ws_env.run(_thread))

    seen: list[dict[str, Any]] = []
    with ws_env.connect(eid, token, after=0) as sock:
        _recv(sock)  # snapshot
        while not seen or seen[-1]["type"] != "approval_required":
            seen.append(_recv(sock, 20.0))
        resumed = ws_env.client.post(
            f"/executions/{eid}/resume",
            json={"decision": "approved"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resumed.status_code == 200, resumed.text
        while seen[-1]["type"] != "workflow_completed":  # pyright: ignore[reportUnnecessaryComparison]
            seen.append(_recv(sock, 20.0))
        with pytest.raises(WebSocketDisconnect) as exc:
            _recv(sock)
        assert exc.value.code == 1000
    assert [m["seq"] for m in seen] == list(range(1, len(seen) + 1))
    types = [m["type"] for m in seen]
    assert types[0] == "workflow_started" and types.count("approval_required") == 1
    assert types.count("workflow_started") == 2 and types[-1] == "workflow_completed"
    assert [m["nodeId"] for m in seen if m["type"] == "node_started"].count("s") == 1


def test_nothing_is_delivered_without_a_notify_and_one_notify_delivers_at_once(
    ws_env: LiveEnv,
) -> None:
    eid, token = ws_env.running_execution()
    with ws_env.connect(eid, token) as sock:
        assert _recv(sock)["type"] == "workflow_started"
        warm = ws_env.append(eid, "node_started", {"node_id": "warm"})
        with _Renotifier(ws_env, eid, warm):  # the listener is certainly registered afterwards
            assert _recv(sock)["seq"] == warm
        # Let late wake-ups from the warm-up's repeated NOTIFYs drain (matters over Neon latency).
        time.sleep(0.5)
        quiet = ws_env.append(eid, "node_started", {"node_id": "quiet"})
        _assert_silent(sock, 1.5)  # persisted, but no wake-up: only the 15 s poll would find it
        started = time.monotonic()
        ws_env.notify(eid, quiet)
        msg = _recv(sock)
        assert msg["seq"] == quiet and msg["node_id"] == "quiet"
        assert time.monotonic() - started < LIVE_DEADLINE


def test_no_listen_connection_is_open_while_no_socket_is_watching(ws_env: LiveEnv) -> None:
    from sqlalchemy import text

    async def _listen_count() -> int:
        async with ws_env.sm() as s:
            return int(
                await s.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND query ILIKE 'LISTEN%'"
                    )
                )
                or 0
            )

    deadline = time.monotonic() + 5.0  # connections of earlier tests may still be closing
    count = ws_env.run(_listen_count)
    while count and time.monotonic() < deadline:
        time.sleep(0.2)
        count = ws_env.run(_listen_count)
    assert count == 0
