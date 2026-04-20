"""Prisma client lifecycle helpers.

A single Prisma client is attached to FastAPI's app.state during lifespan.
Routes and the engine access it via a Depends() helper.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]


@asynccontextmanager
async def prisma_lifespan(  # pyright: ignore[reportUnknownParameterType]
    app: "FastAPI",
) -> AsyncIterator[Prisma]:  # pyright: ignore[reportUnknownParameterType]
    """Connect a Prisma client for the duration of the app's lifespan."""
    db = Prisma()
    await db.connect()
    app.state.db = db
    try:
        yield db
    finally:
        await db.disconnect()


def get_db(request: "Request") -> Prisma:  # pyright: ignore[reportUnknownParameterType]
    """FastAPI dependency — returns the app-wide Prisma client."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise RuntimeError("Prisma client not attached to app.state — did lifespan run?")
    return db


__all__ = ["get_db", "prisma_lifespan"]
