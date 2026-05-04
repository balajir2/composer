"""Prisma client lifecycle helpers.

A single Prisma client is attached to FastAPI's app.state during lifespan.
Routes and the engine access it via a Depends() helper.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from prisma.engine.errors import (  # pyright: ignore[reportMissingImports, reportAttributeAccessIssue]
    EngineConnectionError,
)

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

if TYPE_CHECKING:
    from src.engine.events import ExecutionEventBus
    from src.storage.checkpointer import PrismaCheckpointSaver

logger = logging.getLogger(__name__)

# Neon (and any serverless Postgres) auto-suspends compute after idle.  The
# first connection wakes it, but the wake can take a few seconds; if the
# Prisma query engine subprocess hits that latency it crashes with P1001
# and Python sees `EngineConnectionError`.  We retry the connect a handful
# of times with exponential backoff so the lifespan startup survives a
# cold endpoint without operator intervention.
_CONNECT_MAX_ATTEMPTS = 5
_CONNECT_INITIAL_BACKOFF_SECONDS = 1.0


async def _connect_with_retry(db: Prisma) -> None:  # pyright: ignore[reportUnknownParameterType]
    """Connect Prisma, retrying on EngineConnectionError to handle Neon cold-starts."""
    backoff = _CONNECT_INITIAL_BACKOFF_SECONDS
    last_exc: Exception | None = None
    for attempt in range(1, _CONNECT_MAX_ATTEMPTS + 1):
        try:
            await db.connect()
            if attempt > 1:
                logger.info("prisma: connected on attempt %d", attempt)
            return
        except EngineConnectionError as exc:
            last_exc = exc
            if attempt == _CONNECT_MAX_ATTEMPTS:
                break
            logger.warning(
                "prisma: connect attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                _CONNECT_MAX_ATTEMPTS,
                type(exc).__name__,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
    assert last_exc is not None
    raise last_exc


@asynccontextmanager
async def prisma_lifespan(  # pyright: ignore[reportUnknownParameterType]
    app: FastAPI,
) -> AsyncIterator[Prisma]:  # pyright: ignore[reportUnknownParameterType]
    """Connect a Prisma client + attach checkpointer for the duration of the app's lifespan."""
    # Deferred import to avoid circular dependency: checkpointer imports db module.
    from src.storage.checkpointer import PrismaCheckpointSaver

    db = Prisma()
    await _connect_with_retry(db)
    app.state.db = db
    app.state.checkpointer = PrismaCheckpointSaver(db)
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    from src.security.rate_limit import RateLimiter

    app.state.rate_limiter = RateLimiter()
    try:
        yield db
    finally:
        await db.disconnect()


def get_db(request: Request) -> Prisma:  # pyright: ignore[reportUnknownParameterType]
    """FastAPI dependency — returns the app-wide Prisma client.

    Uses a real (non-TYPE_CHECKING) Request import so FastAPI's parameter
    detection treats this as a dependency rather than a query parameter.
    """
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise RuntimeError("Prisma client not attached to app.state — did lifespan run?")
    return db  # pyright: ignore[reportReturnType]


def get_checkpointer(request: Request) -> "PrismaCheckpointSaver":  # pyright: ignore[reportUnknownParameterType]
    """FastAPI dependency — returns the app-wide PrismaCheckpointSaver."""
    cp = getattr(request.app.state, "checkpointer", None)
    if cp is None:
        raise RuntimeError("Checkpointer not attached to app.state — did lifespan run?")
    return cp


def get_event_bus(request: Request) -> "ExecutionEventBus":  # pyright: ignore[reportUnknownParameterType]
    """FastAPI dependency — returns the app-wide ExecutionEventBus."""
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise RuntimeError("ExecutionEventBus not attached to app.state — did lifespan run?")
    return bus  # pyright: ignore[reportReturnType]


__all__ = ["get_checkpointer", "get_db", "get_event_bus", "prisma_lifespan"]
