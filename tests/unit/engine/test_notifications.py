"""``ExecutionEventListener``: one lazy LISTEN connection per process, targeted wake-ups,
reconnect with wake-all, idle close, direct close on ``aclose``. psycopg is faked."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from src.engine import notifications
from src.engine.events_notify import NOTIFY_CHANNEL
from src.engine.notifications import ExecutionEventListener

CONNINFO = "postgresql://user:hunter2@db.example.com/app"


class FakeConn:
    """An AsyncConnection whose ``notifies()`` yields what the test queues; ``None`` raises."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.executed: list[str] = []
        self.closed = False

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)

    async def notifies(self) -> AsyncIterator[Any]:
        while True:
            item = await self.queue.get()
            if item is None:
                raise OSError("server closed the connection")
            yield SimpleNamespace(payload=item, channel=NOTIFY_CHANNEL)

    async def close(self) -> None:
        self.closed = True


class Factory:
    def __init__(self) -> None:
        self.conns: list[FakeConn] = []
        self.kwargs: list[dict[str, Any]] = []
        self.fail_next = 0

    async def __call__(self, conninfo: str, **kwargs: Any) -> FakeConn:
        assert conninfo == CONNINFO
        self.kwargs.append(kwargs)
        if self.fail_next:
            self.fail_next -= 1
            raise OSError(f"cannot connect to {conninfo}")
        conn = FakeConn()
        self.conns.append(conn)
        return conn

    def listening(self, n: int = 1) -> bool:
        return len(self.conns) >= n and bool(self.conns[n - 1].executed)


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> Factory:
    f = Factory()
    monkeypatch.setattr(notifications.psycopg.AsyncConnection, "connect", f)
    return f


async def _until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def _listener(*, idle_close_seconds: float = 5.0) -> ExecutionEventListener:
    return ExecutionEventListener(
        CONNINFO, idle_close_seconds=idle_close_seconds, backoff_initial=0.01
    )


async def test_no_connection_before_the_first_subscribe(factory: Factory) -> None:
    listener = _listener()
    await asyncio.sleep(0.05)
    assert factory.conns == []
    await listener.aclose()


async def test_connects_with_listen_application_name_and_autocommit(factory: Factory) -> None:
    listener = _listener()
    with listener.subscribe("e1"):
        await _until(factory.listening)
        assert factory.conns[0].executed == [f"LISTEN {NOTIFY_CHANNEL}"]
        kwargs = factory.kwargs[0]
        assert kwargs["autocommit"] is True
        assert kwargs["prepare_threshold"] is None
        assert kwargs["application_name"] == "composer-listen"
    await listener.aclose()


async def test_dispatch_wakes_only_the_matching_execution(factory: Factory) -> None:
    listener = _listener()
    with listener.subscribe("e1") as e1, listener.subscribe("e2") as e2:
        await _until(factory.listening)
        await asyncio.sleep(0.05)
        e1.clear()
        e2.clear()  # the connect wake-all
        factory.conns[0].queue.put_nowait("e1:3")
        await asyncio.wait_for(e1.wait(), 1.0)
        await asyncio.sleep(0.05)
        assert not e2.is_set()
    await listener.aclose()


async def test_two_subscribers_of_one_execution_are_both_woken(factory: Factory) -> None:
    listener = _listener()
    with listener.subscribe("e1") as a, listener.subscribe("e1") as b:
        await _until(factory.listening)
        await asyncio.sleep(0.05)
        a.clear()
        b.clear()
        factory.conns[0].queue.put_nowait("e1:1")
        await asyncio.wait_for(asyncio.gather(a.wait(), b.wait()), 1.0)
    await listener.aclose()


async def test_connect_wakes_all_subscribers(factory: Factory) -> None:
    listener = _listener()
    with listener.subscribe("e1") as e1:
        await asyncio.wait_for(e1.wait(), 1.0)
    await listener.aclose()


async def test_reconnects_after_the_connection_drops_and_wakes_everyone(
    factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    listener = _listener()
    with caplog.at_level("INFO", logger="src.engine.notifications"):
        with listener.subscribe("e1") as e1:
            await _until(factory.listening)
            await asyncio.sleep(0.05)
            e1.clear()
            factory.conns[0].queue.put_nowait(None)  # connection lost
            await _until(lambda: factory.listening(2))
            assert factory.conns[0].closed
            await asyncio.wait_for(e1.wait(), 1.0)  # woken by the reconnect (replay covers gap)
        await listener.aclose()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "OSError" in text
    assert "hunter2" not in text and "db.example.com" not in text
    assert not any(r.exc_info for r in caplog.records)


async def test_retries_a_failing_connect_with_backoff(
    factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    factory.fail_next = 2
    listener = _listener()
    with caplog.at_level("INFO", logger="src.engine.notifications"):
        with listener.subscribe("e1"):
            await _until(factory.listening, timeout=3.0)
        await listener.aclose()
    assert len(factory.kwargs) == 3
    assert not any("hunter2" in r.getMessage() for r in caplog.records)


async def test_closes_idle_after_the_last_unsubscribe(factory: Factory) -> None:
    listener = _listener(idle_close_seconds=0.05)
    with listener.subscribe("e1"):
        await _until(factory.listening)
    await _until(lambda: factory.conns[0].closed)
    # a later subscribe opens a fresh connection
    with listener.subscribe("e2"):
        await _until(lambda: factory.listening(2))
    await listener.aclose()


async def test_still_subscribed_keeps_the_connection_open(factory: Factory) -> None:
    listener = _listener(idle_close_seconds=0.05)
    with listener.subscribe("e1"):
        with listener.subscribe("e2"):
            await _until(factory.listening)
        await asyncio.sleep(0.2)  # e2 left, e1 remains
        assert not factory.conns[0].closed
    await listener.aclose()


async def test_resubscribing_inside_the_idle_window_cancels_the_close(factory: Factory) -> None:
    listener = _listener(idle_close_seconds=0.3)
    with listener.subscribe("e1"):
        await _until(factory.listening)
    with listener.subscribe("e1"):
        await asyncio.sleep(0.5)
        assert not factory.conns[0].closed
        assert len(factory.conns) == 1
    await listener.aclose()


async def test_aclose_closes_the_connection_directly_without_waiting_for_idle(
    factory: Factory,
) -> None:
    listener = _listener(idle_close_seconds=3600.0)
    sub = listener.subscribe("e1")
    sub.__enter__()
    await _until(factory.listening)
    await listener.aclose()
    assert factory.conns[0].closed
    await asyncio.sleep(0.05)
    assert len(factory.conns) == 1  # no reconnect after aclose
    with contextlib.suppress(Exception):
        sub.__exit__(None, None, None)


async def test_aclose_with_a_pending_idle_timer_closes_at_once(factory: Factory) -> None:
    listener = _listener(idle_close_seconds=3600.0)
    with listener.subscribe("e1"):
        await _until(factory.listening)
    await listener.aclose()
    assert factory.conns[0].closed


async def test_aclose_before_any_subscribe_is_a_no_op(factory: Factory) -> None:
    await _listener().aclose()
    assert factory.conns == []


async def test_subscribe_after_aclose_does_not_reconnect(factory: Factory) -> None:
    listener = _listener()
    await listener.aclose()
    with listener.subscribe("e1") as event:
        await asyncio.sleep(0.05)
        assert not event.is_set()
    assert factory.conns == []
