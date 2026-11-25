"""Postgres NOTIFY wake-up signal for execution events (P1-4).

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

NOTIFY goes through a short SQLAlchemy session from the app's pool (it works on Neon's
transaction-mode pooler, and Postgres delivers it when the session commits). Callers send it
only AFTER the event row's own transaction has committed. LISTEN lives in
src/engine/notifications.py on its own direct connection.
"""

from src.db.repos import events as events_repo
from src.db.runtime import get_registered_sessionmaker

NOTIFY_CHANNEL = "composer_execution_events"


async def notify_execution_event(execution_id: str, *, seq: int) -> None:
    """Send a tiny wake-up pointer on the shared NOTIFY channel."""
    sessionmaker = get_registered_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("notify_execution_event called before the database was initialised")
    async with sessionmaker() as session:
        await events_repo.notify(session, NOTIFY_CHANNEL, f"{execution_id}:{seq}")
        await session.commit()


__all__ = ["NOTIFY_CHANNEL", "notify_execution_event"]
