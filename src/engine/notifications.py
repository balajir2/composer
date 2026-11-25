"""One LISTEN connection per process, open only while a WebSocket is watching.

Execution events are durable rows (``src/engine/events_pg.py``); Postgres NOTIFY is only a
wake-up pointer (``"<execution_id>:<seq>"``, ``src/engine/events_notify.py``). The WebSocket loop
waits on an ``asyncio.Event`` that this listener sets, then re-reads the durable store.

Design (plan D4):

* The connection goes to Neon's *direct* endpoint (LISTEN does not survive the transaction-mode
  pooler) and is a plain psycopg connection, not part of any pool.
* It opens on the first ``subscribe`` and closes ``idle_close_seconds`` after the last
  unsubscribe, so an instance nobody is watching never keeps Neon compute awake.
* A notification wakes only the subscribers of that execution.
* On every (re)connect all subscribers are woken, so they re-poll the durable store and nothing
  published during a gap is missed (the WebSocket loop also polls every 15 s regardless).
* ``aclose`` closes the connection itself; it never relies on the idle timer.

Logs carry exception class names only: a psycopg message can contain the connection string.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import psycopg

from src.config import get_settings
from src.db.url import direct_endpoint_url, to_libpq_url
from src.engine.events_notify import NOTIFY_CHANNEL

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from fastapi import FastAPI
    from psycopg import AsyncConnection

logger = logging.getLogger(__name__)

LISTENER_APPLICATION_NAME = "composer-listen"


class ExecutionEventListener:
    """One LISTEN connection per process, open only while someone watches.

    The connection is on the direct endpoint (LISTEN does not survive Neon's transaction-mode
    pooler). It opens on the first subscribe and closes ``idle_close_seconds`` after the last
    unsubscribe, so an idle instance never keeps Neon compute awake. A notification wakes the
    subscribers of that execution only. On every (re)connect all subscribers are woken, so they
    re-poll the durable event store and nothing published during a gap is missed (the WebSocket
    loop also polls every 15 s regardless).
    """

    def __init__(
        self,
        conninfo: str,
        *,
        idle_close_seconds: float = 30.0,
        backoff_initial: float = 0.5,
        backoff_max: float = 30.0,
    ) -> None:
        self._conninfo = conninfo
        self._idle_close_seconds = idle_close_seconds
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._subs: dict[str, set[asyncio.Event]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping: set[asyncio.Task[None]] = set()
        self._conn: AsyncConnection | None = None
        self._idle_timer: asyncio.TimerHandle | None = None
        self._closed = False

    # -- subscribers -----------------------------------------------------------------------

    @contextlib.contextmanager
    def subscribe(self, execution_id: str) -> Generator[asyncio.Event, None, None]:
        """Register for wake-ups of ``execution_id``; the event is set on each notification.

        Must be called on the running loop. After ``aclose`` the event is never set (callers
        still have their poll).
        """
        event = asyncio.Event()
        self._subs.setdefault(execution_id, set()).add(event)
        self._cancel_idle_timer()
        if not self._closed and (self._task is None or self._task.done()):
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="execution-event-listener"
            )
        try:
            yield event
        finally:
            waiters = self._subs.get(execution_id)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    del self._subs[execution_id]
            if not self._subs:
                self._schedule_idle_close()

    # -- lifecycle -------------------------------------------------------------------------

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_idle_close(self) -> None:
        self._cancel_idle_timer()
        if self._task is None:
            return
        self._idle_timer = asyncio.get_running_loop().call_later(
            self._idle_close_seconds, self._idle_close
        )

    def _idle_close(self) -> None:
        self._idle_timer = None
        if self._subs or self._task is None:
            return
        task, self._task = self._task, None
        self._stopping.add(task)
        task.add_done_callback(self._stopping.discard)
        task.cancel()  # _run's finally closes the connection

    async def aclose(self) -> None:
        """Stop for good and close the connection itself (never waits for the idle timer)."""
        self._closed = True
        self._cancel_idle_timer()
        tasks = set(self._stopping)
        if self._task is not None:
            tasks.add(self._task)
            self._task = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._close_conn(self._conn)

    async def _close_conn(self, conn: AsyncConnection | None) -> None:
        """Close ``conn`` (a loop's own connection) and forget it if it is the current one."""
        if conn is None:
            return
        if self._conn is conn:
            self._conn = None
        with contextlib.suppress(Exception):
            await conn.close()

    # -- the connection loop ---------------------------------------------------------------

    def _wake_all(self) -> None:
        for waiters in self._subs.values():
            for event in waiters:
                event.set()

    def _dispatch(self, payload: str) -> None:
        execution_id = payload.rpartition(":")[0]
        for event in self._subs.get(execution_id, ()):
            event.set()

    async def _run(self) -> None:
        backoff = self._backoff_initial
        conn: AsyncConnection | None = None
        try:
            while True:
                try:
                    conn = await psycopg.AsyncConnection.connect(
                        self._conninfo,
                        autocommit=True,
                        prepare_threshold=None,
                        application_name=LISTENER_APPLICATION_NAME,
                    )
                    self._conn = conn
                    # NOTIFY_CHANNEL is a module constant, never user input.
                    await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                    self._wake_all()  # replay covers anything published during a gap
                    backoff = self._backoff_initial
                    async for notification in conn.notifies():
                        self._dispatch(notification.payload)
                    raise ConnectionError("notification stream ended")
                except Exception as exc:
                    logger.warning(
                        "notifications: listener connection lost (%s); retrying in %.1fs",
                        type(exc).__name__,
                        backoff,
                    )
                    await self._close_conn(conn)
                    conn = None
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._backoff_max)
        finally:
            # Cancelled (idle close or aclose): close this loop's own connection. A newer loop
            # may already own `self._conn`, which this must not touch.
            await self._close_conn(conn)


@asynccontextmanager
async def notifications_lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Create the process's listener (it does not connect until a WebSocket subscribes)."""
    settings = get_settings()
    conninfo = (
        to_libpq_url(settings.database_direct_url)
        if settings.database_direct_url
        else direct_endpoint_url(to_libpq_url(settings.database_url))
    )
    listener = ExecutionEventListener(
        conninfo, idle_close_seconds=settings.event_listener_idle_close_seconds
    )
    app.state.event_listener = listener
    try:
        yield
    finally:
        try:
            await listener.aclose()
        except Exception:
            logger.exception("lifespan: event listener close failed during shutdown")


__all__ = ["ExecutionEventListener", "notifications_lifespan"]
