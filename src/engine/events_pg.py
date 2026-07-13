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

from typing import Any, cast

from prisma import Json  # pyright: ignore[reportAttributeAccessIssue]
from src.engine.events import EventType, ExecutionEvent


class PostgresEventStore:
    """Durable, sequence-numbered event log per execution."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def append(self, event: ExecutionEvent) -> int:
        """Persist `event`, returning its assigned sequence number.

        Only `event.payload` (the original, unflattened dict) is stored
        in the `payload` column — not the output of `event.as_json()`,
        which would redundantly re-embed `type`/`executionId`/`timestamp`
        that already have their own columns.

        Sequence numbers start at 1 per execution. Not a single atomic
        INSERT...SELECT COALESCE(MAX(seq),0)+1 to keep the Prisma-ORM
        path simple; a genuine concurrent-write race on the same
        execution's event stream is vanishingly unlikely in practice
        (nodes within one execution run sequentially), but if a race
        is ever observed, tighten via a raw SQL INSERT with a
        window-function-derived seq instead.
        """
        count = await self.db.executionevent.count(where={"executionId": event.execution_id})
        seq = count + 1
        await self.db.executionevent.create(
            data={
                "executionId": event.execution_id,
                "seq": seq,
                "type": event.type,
                "payload": Json(event.payload),
            }
        )
        return seq

    async def list_since(self, execution_id: str, *, after_seq: int) -> list[ExecutionEvent]:
        """Return all events for `execution_id` with seq > after_seq, ascending."""
        rows = await self.db.executionevent.find_many(
            where={"executionId": execution_id, "seq": {"gt": after_seq}},
            order={"seq": "asc"},
        )
        return [
            ExecutionEvent(
                type=cast("EventType", row.type),
                execution_id=row.executionId,
                seq=row.seq,
                payload=row.payload,
            )
            for row in rows
        ]


__all__ = ["PostgresEventStore"]
