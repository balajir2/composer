"""Event repository: per-execution sequence numbers on a real Postgres."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ExecutionEvent
from src.db.repos import events as repo

pytestmark = pytest.mark.integration


def _eid() -> str:
    return f"ex-{secrets.token_hex(6)}"


async def test_serial_appends_get_seq_one_to_n(session: AsyncSession) -> None:
    eid = _eid()
    seqs = [
        await repo.append(session, execution_id=eid, type="node_started", payload={"i": i})
        for i in range(4)
    ]
    assert seqs == [1, 2, 3, 4]


async def test_seq_continues_from_max_after_lowest_rows_deleted(session: AsyncSession) -> None:
    eid = _eid()
    for i in range(4):
        await repo.append(session, execution_id=eid, type="node_started", payload={"i": i})
    await session.execute(
        delete(ExecutionEvent).where(ExecutionEvent.execution_id == eid, ExecutionEvent.seq < 3)
    )
    assert await repo.append(session, execution_id=eid, type="x", payload={}) == 5


async def test_executions_have_independent_sequences(session: AsyncSession) -> None:
    a, b = _eid(), _eid()
    assert await repo.append(session, execution_id=a, type="x", payload={}) == 1
    assert await repo.append(session, execution_id=b, type="x", payload={}) == 1
    assert await repo.append(session, execution_id=a, type="x", payload={}) == 2


async def test_list_since_orders_and_respects_cursor(session: AsyncSession) -> None:
    eid = _eid()
    for i in range(5):
        await repo.append(session, execution_id=eid, type="t", payload={"i": i})
    rows = await repo.list_since(session, eid, after_seq=2)
    assert [r.seq for r in rows] == [3, 4, 5]
    assert [r.payload for r in rows] == [{"i": 2}, {"i": 3}, {"i": 4}]
    assert all(r.execution_id == eid and r.type == "t" for r in rows)
    assert await repo.list_since(session, eid, after_seq=5) == []


async def test_delete_for_executions(session: AsyncSession) -> None:
    a, b, c = _eid(), _eid(), _eid()
    for e in (a, a, b, c):
        await repo.append(session, execution_id=e, type="t", payload={})
    assert await repo.delete_for_executions(session, [a, b]) == 3
    assert await repo.delete_for_executions(session, []) == 0
    assert len(await repo.list_since(session, c, after_seq=0)) == 1


async def test_delete_older_than_is_strict(session: AsyncSession) -> None:
    eid = _eid()
    # A cutoff far in the past so only this test's backdated row can be strictly older.
    cutoff = datetime(1990, 1, 1, tzinfo=UTC)
    offsets = (-1, 0, 1)
    for o in offsets:
        await repo.append(session, execution_id=eid, type="t", payload={"o": o})
    rows = (
        await session.scalars(
            select(ExecutionEvent)
            .where(ExecutionEvent.execution_id == eid)
            .order_by(ExecutionEvent.seq)
        )
    ).all()
    for row, o in zip(rows, offsets, strict=True):
        await session.execute(
            update(ExecutionEvent)
            .where(ExecutionEvent.id == row.id)
            .values(created_at=cutoff + timedelta(seconds=o))
            .execution_options(synchronize_session=False)
        )
    assert await repo.delete_older_than(session, cutoff) == 1
    left = await repo.list_since(session, eid, after_seq=0)
    assert [r.payload for r in left] == [{"o": 0}, {"o": 1}]
