"""Group 8: persisted execution events (``execution_events``) through the event store.

Two tests pinned the old bugs (plan decision D2) and now assert the fix:
``test_append_after_retention_gap_continues_from_max`` (the returned seq is max + 1) and
``test_concurrent_appends_yield_seqs_one_to_n_none_lost``.
The WebSocket half of this group lives in ``test_events_ws.py``.
"""

import asyncio

import pytest

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    Phase4aAdapter,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _execution(db: Db, member: Member) -> str:
    wid = await db.seed_workflow(member)
    return await db.seed_execution(wid, member, status="running")


async def test_serial_appends_get_seq_one_to_n_and_store_payload_unflattened(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    eid = await _execution(db, member)
    other = await _execution(db, member)
    await adapter.append_event(other, "workflow_started", {"status": "running"})
    specs = [
        ("workflow_started", {"status": "running"}),
        ("node_started", {"node_id": "a", "nested": {"k": [1, 2]}}),
        ("node_completed", {"node_id": "a"}),
        ("workflow_completed", {"status": "completed"}),
    ]
    seqs = [await adapter.append_event(eid, t, p) for t, p in specs]
    assert seqs == [1, 2, 3, 4]
    rows = await db.events(eid)
    assert [(r.seq, r.type, r.payload) for r in rows] == [
        (i + 1, t, p) for i, (t, p) in enumerate(specs)
    ]
    assert all(r.execution_id == eid and r.id for r in rows)
    # another execution's stream is independent and untouched
    assert await db.event_seqs(other) == [1]
    assert await adapter.append_event(other, "node_started", {}) == 2


async def test_list_since_returns_seq_after_cursor_ascending(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    eid = await _execution(db, member)
    other = await _execution(db, member)
    # inserted in reverse order so ordering must come from the query, not insertion order
    for seq, t in (
        (5, "workflow_completed"),
        (4, "node_completed"),
        (3, "node_started"),
        (2, "node_completed"),
        (1, "workflow_started"),
    ):
        await db.add_events(eid, [(seq, t, {"seq_is": seq, "status": "x"})])
    await db.add_events(other, [(9, "node_started", {"other": True})])

    everything = await adapter.list_events_since(eid, 0)
    assert [e["seq"] for e in everything] == [1, 2, 3, 4, 5]
    assert everything[0] == {
        "seq": 1,
        "type": "workflow_started",
        "payload": {"seq_is": 1, "status": "x"},
    }
    after2 = await adapter.list_events_since(eid, 2)
    assert [e["seq"] for e in after2] == [3, 4, 5]
    assert [e["type"] for e in after2] == ["node_started", "node_completed", "workflow_completed"]
    assert await adapter.list_events_since(eid, 5) == []
    assert await adapter.list_events_since(eid, 99) == []
    assert await adapter.list_events_since("no-such-execution", 0) == []
    assert [e["seq"] for e in await adapter.list_events_since(other, 0)] == [9]


async def test_append_after_retention_gap_continues_from_max(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    """After the retention sweep deleted the two oldest events the next seq is max + 1 (D2 fix)."""
    eid = await _execution(db, member)
    for i in range(4):
        assert await adapter.append_event(eid, "node_started", {"i": i}) == i + 1
    async with db.sm() as s:
        from sqlalchemy import delete

        from src.db.models import ExecutionEvent as Row

        await s.execute(delete(Row).where(Row.execution_id == eid, Row.seq.in_([1, 2])))
        await s.commit()
    assert await db.event_seqs(eid) == [3, 4]

    assert await adapter.append_event(eid, "node_completed", {"late": True}) == 5
    assert await adapter.append_event(eid, "node_completed", {"late": 2}) == 6
    assert await db.event_seqs(eid) == [3, 4, 5, 6]


async def test_concurrent_appends_yield_seqs_one_to_n_none_lost(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    """N concurrent appends to one execution produce seqs exactly 1..N, none lost (D2 fix)."""
    eid = await _execution(db, member)
    total = 0
    for _ in range(6):
        results = await asyncio.gather(
            *(adapter.append_event(eid, "node_started", {"n": i}) for i in range(8))
        )
        total += len(results)
        assert sorted(results) == list(range(total - 7, total + 1))
    assert await db.event_seqs(eid) == list(range(1, total + 1))
