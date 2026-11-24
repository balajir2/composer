"""Errors raised at the repository boundary."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg.errors
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class AlreadyExists(Exception):
    """A unique constraint rejected the write. ``constraint`` names it when known."""

    def __init__(self, constraint: str | None) -> None:
        super().__init__(f"unique constraint violated: {constraint or 'unknown'}")
        self.constraint = constraint


class ReferencedRowMissing(Exception):
    """A foreign key rejected the write: the row it points at no longer exists."""

    def __init__(self, constraint: str | None) -> None:
        super().__init__(f"referenced row missing: {constraint or 'unknown'}")
        self.constraint = constraint


class StalePrecondition(Exception):
    """An update carried an ``expected_updated_at`` that no longer matches the row.

    ``current_updated_at`` is the row's present value, so the caller can say when it changed.
    """

    def __init__(self, current_updated_at: datetime) -> None:
        super().__init__(f"row changed since {current_updated_at.isoformat()}")
        self.current_updated_at = current_updated_at


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


async def add_or_referenced_row_missing(session: AsyncSession, *rows: object) -> None:
    """Insert ``rows`` in a savepoint, translating a foreign-key violation to ReferencedRowMissing.

    Unique violations are translated to AlreadyExists, as in ``add_or_already_exists``;
    either way only the savepoint is rolled back.
    """
    try:
        async with unique_violation_as_already_exists(session):
            session.add_all(rows)
            await session.flush()
    except IntegrityError as exc:
        if isinstance(exc.orig, psycopg.errors.ForeignKeyViolation):
            raise ReferencedRowMissing(exc.orig.diag.constraint_name) from exc
        raise
