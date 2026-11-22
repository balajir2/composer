"""Errors raised at the repository boundary."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg.errors
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class AlreadyExists(Exception):
    """A unique constraint rejected the write. ``constraint`` names it when known."""

    def __init__(self, constraint: str | None) -> None:
        super().__init__(f"unique constraint violated: {constraint or 'unknown'}")
        self.constraint = constraint


@asynccontextmanager
async def unique_violation_as_already_exists(session: AsyncSession) -> AsyncGenerator[None]:
    """Run the block in a savepoint, translating unique violations to AlreadyExists.

    A violation rolls back only the savepoint, so the caller's transaction stays
    usable. Other integrity errors propagate unchanged.
    """
    try:
        async with session.begin_nested():
            yield
    except IntegrityError as exc:
        if isinstance(exc.orig, psycopg.errors.UniqueViolation):
            raise AlreadyExists(exc.orig.diag.constraint_name) from exc
        raise


async def add_or_already_exists(session: AsyncSession, *rows: object) -> None:
    """Insert ``rows`` in a savepoint, translating unique violations to AlreadyExists.

    The rows are added inside the savepoint (begin_nested() flushes anything
    already pending first), so a duplicate rolls back only the savepoint and
    the caller's transaction stays usable. Callers must not have other
    unflushed changes they expect to survive a duplicate.
    """
    async with unique_violation_as_already_exists(session):
        session.add_all(rows)
        await session.flush()
