"""Execution event bus — in-process asyncio fanout for WebSocket streaming.

Event shapes match IE's DES-007 protocol (Phase 9a spec §7.2).

See Phase 9 spec §7 + ADR-0022.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EventType = Literal[
    "workflow_started",
    "node_started",
    "node_completed",
    "node_failed",
    "workflow_completed",
    "approval_required",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ExecutionEvent:
    type: EventType
    execution_id: str = field(metadata={"alias": "executionId"})
    tenant_id: str | None = field(default=None, metadata={"alias": "tenantId"})
    timestamp: str = field(default_factory=_now_iso)
    payload: dict[str, Any] = field(default_factory=dict[str, Any])
    # P1-4: populated by PostgresEventStore.list_since when reconstructing
    # a persisted event; None for a freshly-constructed event that hasn't
    # been assigned a sequence number yet (PostgresEventStore.append
    # returns the assigned seq separately rather than mutating the frozen
    # dataclass in place).
    seq: int | None = field(default=None)

    def as_json(self) -> dict[str, Any]:
        """Returns DES-007-shape dict with camelCase keys."""
        out: dict[str, Any] = {
            "type": self.type,
            "executionId": self.execution_id,
            "tenantId": self.tenant_id,
            "timestamp": self.timestamp,
            "seq": self.seq,
        }
        out.update(self.payload)
        return out


class ExecutionEventBus:
    """Per-execution fanout of ExecutionEvent, in-process only.

    Bounded queue per subscriber (maxsize=128); on overflow the oldest
    undelivered event is dropped so a slow subscriber never stalls the
    emitter.
    """

    _MAX_QUEUE = 128

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[ExecutionEvent | None]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, execution_id: str) -> asyncio.Queue[ExecutionEvent | None]:
        q: asyncio.Queue[ExecutionEvent | None] = asyncio.Queue(maxsize=self._MAX_QUEUE)
        async with self._lock:
            self._queues.setdefault(execution_id, []).append(q)
        return q

    async def unsubscribe(self, execution_id: str, q: asyncio.Queue[ExecutionEvent | None]) -> None:
        async with self._lock:
            queues = self._queues.get(execution_id, [])
            if q in queues:
                queues.remove(q)
            if not queues:
                self._queues.pop(execution_id, None)

    async def emit(self, event: ExecutionEvent) -> None:
        async with self._lock:
            queues = list(self._queues.get(event.execution_id, []))
        for q in queues:
            self._offer(q, event)

    @staticmethod
    def _offer(q: asyncio.Queue[ExecutionEvent | None], event: ExecutionEvent) -> None:
        try:
            q.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            return
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(event)

    async def close(self, execution_id: str) -> None:
        async with self._lock:
            queues = list(self._queues.get(execution_id, []))
        for q in queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)


__all__ = ["EventType", "ExecutionEvent", "ExecutionEventBus"]
