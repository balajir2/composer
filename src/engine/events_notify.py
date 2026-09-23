"""Postgres LISTEN/NOTIFY wake-up signal for execution events (P1-4).

Verified characteristics (not assumed — see ADR-0033): NOTIFY payloads
are capped at 8000 bytes and are NOT durable (a notification is lost if
no one is listening at emit time). This module therefore sends only a
tiny pointer — "<execution_id>:<seq>" — never the event body itself.
The WebSocket handler (src/api/events_ws.py) reacts to a notification
by querying src/engine/events_pg.py's PostgresEventStore for everything
after its last-seen seq, which is the durable source of truth. A missed
NOTIFY (client not yet subscribed, or a brief connection gap) is
harmless: the same query catches up on the next notification or
keepalive poll.

Uses a single dedicated asyncpg connection for LISTEN, separate from
the Prisma-managed pool — Prisma's client doesn't expose an async
LISTEN callback API.
"""

from __future__ import annotations

import asyncio

import asyncpg

from src.config import get_settings

NOTIFY_CHANNEL = "composer_execution_events"

_notify_connection: asyncpg.Connection | None = None
_connection_lock = asyncio.Lock()


async def _get_notify_connection() -> asyncpg.Connection:
    """Lazily create (and cache) the dedicated NOTIFY connection."""
    global _notify_connection
    if _notify_connection is not None and not _notify_connection.is_closed():
        return _notify_connection
    async with _connection_lock:
        # Double-check inside the lock in case another concurrent caller
        # already established the connection while we were waiting.
        if _notify_connection is None or _notify_connection.is_closed():
            _notify_connection = await asyncpg.connect(get_settings().database_url)
        return _notify_connection


async def notify_execution_event(execution_id: str, *, seq: int) -> None:
    """Send a tiny wake-up pointer on the shared NOTIFY channel."""
    conn = await _get_notify_connection()
    payload = f"{execution_id}:{seq}"
    await conn.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, payload)


async def close_notify_connection() -> None:
    """Close the dedicated NOTIFY connection (call on app shutdown)."""
    global _notify_connection
    if _notify_connection is not None and not _notify_connection.is_closed():
        await _notify_connection.close()
    _notify_connection = None


__all__ = ["NOTIFY_CHANNEL", "close_notify_connection", "notify_execution_event"]
