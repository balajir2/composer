"""Tests for persisted execution events (P1-4)."""

from unittest.mock import AsyncMock, MagicMock

from src.engine.events import ExecutionEvent
from src.engine.events_pg import PostgresEventStore


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.executionevent = MagicMock()
    return db


async def test_append_assigns_incrementing_sequence_numbers() -> None:
    db = _mock_db()
    db.executionevent.count = AsyncMock(side_effect=[0, 1])
    db.executionevent.create = AsyncMock()
    store = PostgresEventStore(db)

    e1 = ExecutionEvent(type="node_started", execution_id="ex1", payload={"nodeId": "n1"})
    e2 = ExecutionEvent(type="node_completed", execution_id="ex1", payload={"nodeId": "n1"})
    seq1 = await store.append(e1)
    seq2 = await store.append(e2)

    assert seq1 == 1
    assert seq2 == 2
    assert db.executionevent.create.await_count == 2


async def test_list_since_returns_events_after_cursor() -> None:
    db = _mock_db()
    db.executionevent.find_many = AsyncMock(
        return_value=[
            MagicMock(seq=3, type="node_completed", executionId="ex1", payload={"a": 1}),
            MagicMock(seq=4, type="workflow_completed", executionId="ex1", payload={"b": 2}),
        ]
    )
    store = PostgresEventStore(db)
    events = await store.list_since("ex1", after_seq=2)
    assert [e.type for e in events] == ["node_completed", "workflow_completed"]
    assert [e.seq for e in events] == [3, 4]
    db.executionevent.find_many.assert_awaited_once_with(
        where={"executionId": "ex1", "seq": {"gt": 2}},
        order={"seq": "asc"},
    )
