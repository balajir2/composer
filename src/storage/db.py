"""Prisma client lifecycle helpers.

A single Prisma client is attached to FastAPI's app.state during lifespan.
Routes and the engine access it via a Depends() helper.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

if TYPE_CHECKING:
    from src.engine.events import ExecutionEventBus
    from src.storage.checkpointer import PrismaCheckpointSaver


@asynccontextmanager
async def prisma_lifespan(  # pyright: ignore[reportUnknownParameterType]
    app: FastAPI,
) -> AsyncIterator[Prisma]:  # pyright: ignore[reportUnknownParameterType]
    """Connect a Prisma client + attach checkpointer for the duration of the app's lifespan."""
    # Deferred import to avoid circular dependency: checkpointer imports db module.
    from src.storage.checkpointer import PrismaCheckpointSaver

    db = Prisma()
    await db.connect()
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
