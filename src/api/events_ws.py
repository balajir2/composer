"""GET /executions/{id}/ws — WebSocket stream of execution events.

Replaces Phase 5b SSE (src/api/events.py, deleted in Task 8).
Event shapes per DES-007 — see src/engine/events.py.

See Phase 9 spec §7.
"""

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from src.config import Settings, get_settings
from src.engine.events import ExecutionEvent, ExecutionEventBus
from src.security.auth import AuthError, verify_user_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events-ws"])


async def _authenticate_ws(ws: WebSocket, settings: Settings) -> str | None:
    """Validate JWT from Sec-WebSocket-Protocol subprotocol.

    Client connects with `new WebSocket(url, ['bearer', <token>])`.
    Server sees the protocols as `ws.headers['sec-websocket-protocol']`
    = 'bearer, <token>'.  Returns user_id on success; None on failure
    (caller should close with code 4401).
    """
    header = ws.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in header.split(",") if p.strip()]
    if len(parts) != 2 or parts[0] != "bearer":
        return None
    token = parts[1]
    try:
        return await verify_user_token(token, settings)
    except AuthError:
        return None


@router.websocket("/executions/{execution_id}/ws")
async def events_ws(  # pyright: ignore[reportUnusedFunction]
    ws: WebSocket,
    execution_id: str,
) -> None:
    # Resolve app-level singletons directly from ws.app.state
    # (WebSocket is HTTPConnection, not Request — Depends(get_db) won't work here)
    settings: Settings = get_settings()
    db = getattr(ws.app.state, "db", None)
    if db is None:
        await ws.close(code=4500, reason="server error")
        return
    event_bus: ExecutionEventBus | None = getattr(ws.app.state, "event_bus", None)
    if event_bus is None:
        await ws.close(code=4500, reason="server error")
        return

    # 1) authenticate
    user_id = await _authenticate_ws(ws, settings)
    if user_id is None:
        await ws.close(code=4401, reason="unauthenticated")
        return

    # 2) authorize
    execution = await db.workflowexecution.find_unique(where={"id": execution_id})  # pyright: ignore[reportAttributeAccessIssue]
    if execution is None:
        await ws.close(code=4404, reason="not found")
        return

    # fetch role (admin bypass)
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if settings.deployment_mode == "standalone" and (
        user is None or getattr(user, "isActive", True) is False
    ):
        await ws.close(code=4401, reason="unauthenticated")
        return
    role = getattr(user, "role", None)
    role_str = (
        str(role.value) if role is not None and hasattr(role, "value") else str(role or "member")
    )
    if role_str != "admin" and execution.userId != user_id:
        await ws.close(code=4403, reason="forbidden")
        return

    # 3) accept with subprotocol echo
    await ws.accept(subprotocol="bearer")

    # 4) snapshot
    terminal = {"completed", "failed", "canceled"}
    if execution.status == "waiting_approval":
        snapshot_type: Any = "approval_required"
    elif execution.status in terminal:
        snapshot_type = "workflow_completed"
    else:
        snapshot_type = "workflow_started"
    snapshot = ExecutionEvent(
        type=snapshot_type, execution_id=execution_id, payload={"status": execution.status}
    )
    await ws.send_text(json.dumps(snapshot.as_json()))
    if execution.status in terminal:
        await ws.close(code=status.WS_1000_NORMAL_CLOSURE, reason="terminal")
        return

    # 5) subscribe + fan out
    queue = await event_bus.subscribe(execution_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                # keepalive ping
                with contextlib.suppress(Exception):
                    await ws.send_json({"type": "__keepalive__"})
                continue
            if event is None:
                break
            await ws.send_text(json.dumps(event.as_json()))
            if event.type == "workflow_completed" and event.payload.get("status") in (
                "failed",
                "completed",
            ):
                break
    except WebSocketDisconnect:
        return
    finally:
        await event_bus.unsubscribe(execution_id, queue)
        with contextlib.suppress(Exception):
            await ws.close(code=status.WS_1000_NORMAL_CLOSURE)


__all__ = ["router"]
