"""Group 8: persisted execution events (``execution_events``) through the event store.

Two tests pin TODAY'S BUGS (plan decision D2) and are the ones Task 3 changes:
* ``test_append_after_retention_gap_today_collides`` becomes
  ``test_append_after_retention_gap_continues_from_max`` (the returned seq is max + 1).
* ``test_concurrent_appends_today_lose_events`` becomes "N concurrent appends yield seqs exactly
  1..N, no error".
The WebSocket half of this group lives in ``test_events_ws.py``.
"""

import asyncio

import pytest

from tests.integration.sqla_phase4a.conftest import (
    DUPLICATE_KEY_ERRORS,
    HTTPX_MARK,
    Db,
    DuplicateKey,
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


async def test_append_after_retention_gap_today_collides(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    """D2 bug (b), pinned: today seq = count + 1, so after the retention sweep deleted the two
    oldest events the next seq equals an existing one and EVERY later append is lost."""
    eid = await _execution(db, member)
    for i in range(4):
        assert await adapter.append_event(eid, "node_started", {"i": i}) == i + 1
    async with db.sm() as s:
        from sqlalchemy import delete

        from src.db.models import ExecutionEvent as Row

        await s.execute(delete(Row).where(Row.execution_id == eid, Row.seq.in_([1, 2])))
        await s.commit()
    assert await db.event_seqs(eid) == [3, 4]

    for _ in range(2):  # not a one-off: it keeps failing
        with pytest.raises(DUPLICATE_KEY_ERRORS):
            await adapter.append_event(eid, "node_completed", {"late": True})
    assert await db.event_seqs(eid) == [3, 4]
    assert [e.payload for e in await db.events(eid)] == [{"i": 2}, {"i": 3}]


async def test_concurrent_appends_today_lose_events(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    """D2 bug (a), pinned: parallel appends to one execution race on count + 1; the losers hit
    the (execution_id, seq) unique index and their events are lost."""
    eid = await _execution(db, member)
    attempts = 0
    failures = 0
    for _ in range(6):
        results = await asyncio.gather(
            *(adapter.append_event(eid, "node_started", {"n": i}) for i in range(8)),
            return_exceptions=True,
        )
        attempts += len(results)
        failures += sum(isinstance(r, DuplicateKey) for r in results)
        assert all(isinstance(r, (int, DuplicateKey)) for r in results), results
    seqs = await db.event_seqs(eid)
    assert failures >= 1, "expected at least one lost append today (D2)"
    assert len(seqs) == attempts - failures
    assert len(set(seqs)) == len(seqs)  # unique, but with gaps/losses
