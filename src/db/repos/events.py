"""Execution events: per-execution sequence numbers, replay reads and retention deletes.

Callers own the transaction. ``append`` is the one documented exception to the
non-blocking rule of ``src/db/locks.py``: it takes a short blocking advisory lock
so concurrent appends to one execution serialize and get seqs 1..N without gaps.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.ids import new_id
from src.db.models import ExecutionEvent
from src.db.repos._sql import in_array
from src.db.types import utcnow

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult


@dataclass(frozen=True)
class EventRecord:
    id: str
    execution_id: str
    seq: int
    type: str
    payload: Any
    created_at: datetime


async def append(session: AsyncSession, *, execution_id: str, type: str, payload: Any) -> int:
    """Insert the next event of ``execution_id`` and return its seq (max + 1, from 1).

    The lock is transaction-scoped and per execution, so it is released by the caller's
    commit; keep the transaction short (append, then commit at once).
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"execution_events:{execution_id}"},
    )
    next_seq = (
        select(func.coalesce(func.max(ExecutionEvent.seq), 0) + 1)
        .where(ExecutionEvent.execution_id == execution_id)
        .scalar_subquery()
    )
    seq = await session.scalar(
        insert(ExecutionEvent)
        .values(
            id=new_id(),
            execution_id=execution_id,
            seq=next_seq,
            type=type,
            payload=payload,
            created_at=utcnow(),
        )
        .returning(ExecutionEvent.seq)
    )
    assert seq is not None
    return seq


async def notify(session: AsyncSession, channel: str, payload: str) -> None:
    """``pg_notify(channel, payload)``; delivered when the caller's transaction commits."""
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": payload}
    )


async def list_since(
    session: AsyncSession, execution_id: str, *, after_seq: int
) -> list[EventRecord]:
    """Events of ``execution_id`` with seq > ``after_seq``, ascending."""
    rows = await session.scalars(
        select(ExecutionEvent)
        .where(ExecutionEvent.execution_id == execution_id, ExecutionEvent.seq > after_seq)
        .order_by(ExecutionEvent.seq)
    )
    return [EventRecord(r.id, r.execution_id, r.seq, r.type, r.payload, r.created_at) for r in rows]


async def delete_for_executions(session: AsyncSession, execution_ids: list[str]) -> int:
    """Delete every event of the given executions; returns the number deleted."""
    if not execution_ids:
        return 0
    result = await session.execute(
        sa_delete(ExecutionEvent)
        .where(in_array(ExecutionEvent.execution_id, execution_ids))
        .execution_options(synchronize_session=False)
    )
    return cast("CursorResult[Any]", result).rowcount


async def delete_older_than(session: AsyncSession, cutoff: datetime) -> int:
    """Delete events created strictly before ``cutoff``; returns the number deleted."""
    result = await session.execute(
        sa_delete(ExecutionEvent)
        .where(ExecutionEvent.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    return cast("CursorResult[Any]", result).rowcount
