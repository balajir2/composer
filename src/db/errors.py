"""Errors raised at the repository boundary."""

import psycopg.errors
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class AlreadyExists(Exception):
    """A unique constraint rejected the write. ``constraint`` names it when known."""

    def __init__(self, constraint: str | None) -> None:
        super().__init__(f"unique constraint violated: {constraint or 'unknown'}")
        self.constraint = constraint


async def flush_or_already_exists(session: AsyncSession) -> None:
    """Flush pending inserts in a savepoint, translating unique violations.

    The savepoint means a duplicate leaves the caller's transaction usable,
    so a route can still answer 409 and keep working with the session.
    """
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if isinstance(exc.orig, psycopg.errors.UniqueViolation):
            raise AlreadyExists(exc.orig.diag.constraint_name) from exc
        raise
