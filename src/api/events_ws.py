"""GET /executions/{id}/ws — WebSocket stream of execution events.

Replaces Phase 5b SSE (src/api/events.py, deleted in Task 8).
Event shapes per DES-007 — see src/engine/events.py.

See Phase 9 spec §7.
"""

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from src.config import Settings, get_settings
from src.db.repos import executions as executions_repo
from src.db.repos import users as users_repo
from src.db.runtime import request_sessionmaker
from src.engine.events import ExecutionEvent
from src.security.auth import AuthError, verify_user_token
from src.telemetry.providers import (
    websocket_connection,
    websocket_replay,
    websocket_send_failed,
)

if TYPE_CHECKING:
    from src.engine.events_pg import PostgresEventStore
    from src.engine.notifications import ExecutionEventListener

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events-ws"])

# Statuses that mark a workflow_completed event (or execution row) as final —
# shared by the snapshot check, replay loop, and live loop so the three
# don't drift out of sync with each other (P1-4 code review, Minor #1).
_TERMINAL_STATUSES = {"completed", "failed", "canceled"}


def _is_terminal_event(event: ExecutionEvent) -> bool:
    """True if `event` is the terminal workflow_completed marker."""
    return event.type == "workflow_completed" and event.payload.get("status") in _TERMINAL_STATUSES


async def _send_text(ws: WebSocket, text: str) -> None:
    try:
        await ws.send_text(text)
    except BaseException:
        websocket_send_failed()
        raise


async def _send_json(ws: WebSocket, value: object) -> None:
    try:
        await ws.send_json(value)
    except BaseException:
        websocket_send_failed()
        raise


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
    after: int = Query(default=0, ge=0),  # reconnect cursor (P1-4): replay events with seq > after
) -> None:
    # Resolve app-level singletons directly from ws.app.state
    # (a WebSocket is an HTTPConnection, not a Request, so request dependencies don't apply)
    settings: Settings = get_settings()
    sessionmaker = request_sessionmaker(ws)
    if sessionmaker is None:
        await ws.close(code=4500, reason="server error")
        return
    event_store: PostgresEventStore | None = getattr(ws.app.state, "event_bus", None)
    if event_store is None:
        await ws.close(code=4500, reason="server error")
        return

    # 1) authenticate
    user_id = await _authenticate_ws(ws, settings)
    if user_id is None:
        await ws.close(code=4401, reason="unauthenticated")
        return

    # 2) authorize
    # Short session, closed before the socket is accepted or waited on.
    async with sessionmaker() as session:
        execution = await executions_repo.get(session, execution_id)
    if execution is None:
        await ws.close(code=4404, reason="not found")
        return

    # fetch role (admin bypass)
    async with sessionmaker() as session:
        user = await users_repo.get_by_id(session, user_id)
    if settings.deployment_mode == "standalone" and (user is None or not user.is_active):
        await ws.close(code=4401, reason="unauthenticated")
        return
    role_str = user.role.value if user is not None else "member"
    if role_str != "admin" and execution.user_id != user_id:
        await ws.close(code=4403, reason="forbidden")
        return

    # 3) accept with subprotocol echo
    await ws.accept(subprotocol="bearer")

    with websocket_connection(reconnected=after > 0):
        # 4) snapshot
        if execution.status == "waiting_approval":
            snapshot_type: Any = "approval_required"
        elif execution.status in _TERMINAL_STATUSES:
            snapshot_type = "workflow_completed"
        else:
            snapshot_type = "workflow_started"
        snapshot = ExecutionEvent(
            type=snapshot_type, execution_id=execution_id, payload={"status": execution.status}
        )
        await _send_text(ws, json.dumps(snapshot.as_json()))
        if execution.status in _TERMINAL_STATUSES:
            await ws.close(code=status.WS_1000_NORMAL_CLOSURE, reason="terminal")
            return

        # 5) replay: catch the client up on anything persisted since its cursor
        # (reconnect after a dropped connection, or a client that missed a
        # terminal event while briefly disconnected — P1-4).
        last_seq = after
        missed = await event_store.list_since(execution_id, after_seq=after)
        websocket_replay(len(missed))
        for event in missed:
            await _send_text(ws, json.dumps(event.as_json()))
            if event.seq is not None:
                last_seq = event.seq
            if _is_terminal_event(event):
                await ws.close(code=status.WS_1000_NORMAL_CLOSURE, reason="terminal")
                return

        # 6) live delivery via Postgres LISTEN/NOTIFY: the process-wide listener
        # (src/engine/notifications.py) sets `notified` when a NOTIFY names THIS
        # execution, and on every (re)connect of its LISTEN connection; the loop
        # then re-polls the durable store. Without a listener (unit tests) the
        # 15 s poll below still delivers everything.
        listener: ExecutionEventListener | None = getattr(ws.app.state, "event_listener", None)
        with (
            listener.subscribe(execution_id)
            if listener is not None
            else contextlib.nullcontext(asyncio.Event())
        ) as notified:
            try:
                while True:
                    got_notification = True
                    try:
                        await asyncio.wait_for(notified.wait(), timeout=15.0)
                        notified.clear()
                    except TimeoutError:
                        got_notification = False

                    # The 15s timeout doubles as a poll trigger, not just a keepalive
                    # tick: events_notify.py's module docstring promises a missed
                    # NOTIFY is caught up "on the next notification or keepalive
                    # poll" — so query list_since on every iteration, timeout or not
                    # (P1-4 code review, Critical).
                    new_events = await event_store.list_since(execution_id, after_seq=last_seq)
                    for event in new_events:
                        await _send_text(ws, json.dumps(event.as_json()))
                        if event.seq is not None:
                            last_seq = event.seq
                        if _is_terminal_event(event):
                            return

                    if not got_notification:
                        # This send is the loop's only opportunity to detect a
                        # disconnected client (it never calls ws.receive()). A
                        # failure here means the client is gone — break instead of
                        # swallowing the error and looping forever, which would keep
                        # this subscription (and so the shared LISTEN connection) open
                        # (P1-4 code review, Important #3).
                        try:
                            await _send_json(ws, {"type": "__keepalive__"})
                        except Exception:
                            logger.warning(
                                "events_ws: keepalive send failed for execution %s, closing",
                                execution_id,
                            )
                            return
            except WebSocketDisconnect:
                return
            finally:
                with contextlib.suppress(Exception):
                    await ws.close(code=status.WS_1000_NORMAL_CLOSURE)


__all__ = ["router"]
