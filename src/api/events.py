"""GET /executions/{id}/events — Server-Sent Events stream.

See Phase 5b spec §7.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.engine.events import ExecutionEvent, ExecutionEventBus
from src.security.auth import get_current_user_id
from src.storage.db import get_db, get_event_bus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

router = APIRouter(tags=["events"])


def _sse_format(event: ExecutionEvent) -> bytes:
    payload = json.dumps(event.as_json())
    return f"event: {event.type}\ndata: {payload}\n\n".encode()


@router.get("/executions/{execution_id}/events")
async def stream_events(
    execution_id: str,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    event_bus: ExecutionEventBus = Depends(get_event_bus),
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id}
    )
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if execution.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )

    queue = await event_bus.subscribe(execution_id)
    terminal_statuses = {"completed", "failed"}

    async def _gen() -> AsyncIterator[bytes]:
        try:
            snapshot = ExecutionEvent(
                type="status-change",
                execution_id=execution_id,
                payload={"status": execution.status, "previous": None},
            )
            yield _sse_format(snapshot)

            if execution.status in terminal_statuses:
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event: Any = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is None:
                    return
                yield _sse_format(event)
        finally:
            await event_bus.unsubscribe(execution_id, queue)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
