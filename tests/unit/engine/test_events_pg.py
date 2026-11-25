"""Tests for persisted execution events (P1-4)."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.db.repos import events as events_repo
from src.engine.events import ExecutionEvent
from src.engine.events_pg import PostgresEventStore
from tests.unit.db_fakes import FakeSessionmaker, RecordingSessionmaker


async def test_append_stores_payload_and_returns_repo_seq(monkeypatch: pytest.MonkeyPatch) -> None:
    sm = FakeSessionmaker()
    append = AsyncMock(side_effect=[1, 2])
    monkeypatch.setattr(events_repo, "append", append)
    store = PostgresEventStore(sm)  # pyright: ignore[reportArgumentType]

    e1 = ExecutionEvent(type="node_started", execution_id="ex1", payload={"nodeId": "n1"})
    e2 = ExecutionEvent(type="node_completed", execution_id="ex1", payload={"nodeId": "n1"})
    assert await store.append(e1) == 1
    assert await store.append(e2) == 2

    assert append.await_args_list[0].kwargs == {
        "execution_id": "ex1",
        "type": "node_started",
        "payload": {"nodeId": "n1"},
    }
    assert sm.session.commit.await_count == 2


async def test_append_commits_before_its_session_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    sm = RecordingSessionmaker(events)

    async def _append(*_a: Any, **_k: Any) -> int:
        events.append("db:append")
        return 1

    async def _commit() -> None:
        events.append("db:commit")

    monkeypatch.setattr(events_repo, "append", _append)
    sm.session.commit = _commit  # pyright: ignore[reportAttributeAccessIssue]
    store = PostgresEventStore(sm)  # pyright: ignore[reportArgumentType]
    await store.append(ExecutionEvent(type="node_started", execution_id="ex1", payload={}))
    events.append("notify")
    assert events == ["session:enter", "db:append", "db:commit", "session:exit", "notify"]


async def test_list_since_returns_events_after_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    rows = [
        events_repo.EventRecord("i3", "ex1", 3, "node_completed", {"a": 1}, now),
        events_repo.EventRecord("i4", "ex1", 4, "workflow_completed", {"b": 2}, now),
    ]
    list_since = AsyncMock(return_value=rows)
    monkeypatch.setattr(events_repo, "list_since", list_since)
    store = PostgresEventStore(FakeSessionmaker())  # pyright: ignore[reportArgumentType]
    events = await store.list_since("ex1", after_seq=2)
    assert [e.type for e in events] == ["node_completed", "workflow_completed"]
    assert [e.seq for e in events] == [3, 4]
    assert [e.payload for e in events] == [{"a": 1}, {"b": 2}]
    assert list_since.await_args is not None
    assert list_since.await_args.args[1] == "ex1"
    assert list_since.await_args.kwargs == {"after_seq": 2}


@pytest.fixture
def ordering(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], PostgresEventStore]:
    """A real PostgresEventStore over a recording sessionmaker; NOTIFY is a network event."""
    events: list[str] = []
    sm = RecordingSessionmaker(events)

    async def _append(*_a: Any, **_k: Any) -> int:
        return 1

    async def _notify(execution_id: str, *, seq: int) -> None:
        events.append("net:notify")

    monkeypatch.setattr(events_repo, "append", _append)
    monkeypatch.setattr("src.engine.langgraph_executor.notify_execution_event", _notify)
    monkeypatch.setattr("src.engine.events_wrapper.notify_execution_event", _notify)
    return events, PostgresEventStore(sm)  # pyright: ignore[reportArgumentType]


async def test_executor_emit_notifies_after_the_session_closes(
    ordering: tuple[list[str], PostgresEventStore],
) -> None:
    from src.engine.langgraph_executor import LangGraphExecutor

    events, store = ordering
    executor = LangGraphExecutor(
        sessionmaker=cast("Any", None), checkpointer=cast("Any", None), event_bus=store
    )
    await executor._emit("workflow_started", "ex1", {})  # pyright: ignore[reportPrivateUsage]
    assert events == ["session:enter", "session:exit", "net:notify"]


async def test_wrapper_persist_and_notify_notifies_after_the_session_closes(
    ordering: tuple[list[str], PostgresEventStore],
) -> None:
    from src.engine.events_wrapper import _persist_and_notify  # pyright: ignore[reportPrivateUsage]

    events, store = ordering
    event = ExecutionEvent(type="node_started", execution_id="ex1", payload={})
    await _persist_and_notify(store, "ex1", event)
    assert events == ["session:enter", "session:exit", "net:notify"]
