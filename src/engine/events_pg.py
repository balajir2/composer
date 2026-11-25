"""Persisted execution events (P1-4).

Replaces src/engine/events.py's ExecutionEventBus in-process asyncio
fan-out with a Postgres-backed store: every event is appended to the
`execution_events` table with a per-execution incrementing sequence
number, giving reconnect-cursor support (a client can ask for
"everything after seq N") and missed-terminal-event recovery that the
in-process version could not provide (ADR-0033).

Low-latency delivery to already-connected WebSocket clients is handled
separately by Postgres LISTEN/NOTIFY (src/engine/events_notify.py) —
this module is the durable record; that module is the wake-up signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from src.db.repos import events as events_repo
from src.engine.events import EventType, ExecutionEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class PostgresEventStore:
    """Durable, sequence-numbered event log per execution."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def append(self, event: ExecutionEvent) -> int:
        """Persist `event`, returning its assigned sequence number.

        Only `event.payload` (the original, unflattened dict) is stored
        in the `payload` column — not the output of `event.as_json()`,
        which would redundantly re-embed `type`/`executionId`/`timestamp`
        that already have their own columns.

        Sequence numbers start at 1 per execution and continue from the current
        maximum (the repository serializes concurrent appends). Runs in its own short
        session and commits before returning, so callers may NOTIFY afterwards.
        """
        async with self.sessionmaker() as session:
            seq = await events_repo.append(
                session,
                execution_id=event.execution_id,
                type=event.type,
                payload=event.payload,
            )
            await session.commit()
        return seq

    async def list_since(self, execution_id: str, *, after_seq: int) -> list[ExecutionEvent]:
        """Return all events for `execution_id` with seq > after_seq, ascending."""
        async with self.sessionmaker() as session:
            rows = await events_repo.list_since(session, execution_id, after_seq=after_seq)
        return [
            ExecutionEvent(
                type=cast("EventType", row.type),
                execution_id=row.execution_id,
                seq=row.seq,
                payload=row.payload,
            )
            for row in rows
        ]


__all__ = ["PostgresEventStore"]
