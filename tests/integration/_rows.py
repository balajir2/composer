"""Seed and inspect rows in integration tests that drive the real app.

Replaces the test-only Prisma client that tests/conftest.py gave NEEDS_LEGACY_PRISMA modules
until phase 6. Uses the app's own sessionmaker, so rows are committed before the code under
test reads them. ORM objects come back detached with their columns loaded; relationships are
lazy="raise", so read only columns.
"""

from typing import Any, TypeVar, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from src.db.repos import checkpoints as checkpoints_repo

T = TypeVar("T")


class Rows:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sm

    async def add(self, row: T) -> T:
        async with self._sm() as s:
            s.add(row)
            await s.commit()
            await s.refresh(row)
            s.expunge(row)
            return row

    async def get(self, model: type[T], pk: Any) -> T | None:
        async with self._sm() as s:
            row = await s.get(model, pk)
            if row is not None:
                s.expunge(row)
            return row

    async def where(self, model: type[T], *criteria: ColumnElement[bool]) -> list[T]:
        async with self._sm() as s:
            found = list((await s.scalars(select(model).where(*criteria))).all())
            for row in found:
                s.expunge(row)
            return found

    async def count(self, model: type[Any], *criteria: ColumnElement[bool]) -> int:
        async with self._sm() as s:
            return (await s.scalar(select(func.count()).select_from(model).where(*criteria))) or 0

    async def set(self, model: type[Any], *criteria: ColumnElement[bool], **values: Any) -> int:
        async with self._sm() as s:
            result = cast(
                "CursorResult[Any]",
                await s.execute(update(model).where(*criteria).values(**values)),
            )
            await s.commit()
            return result.rowcount

    async def remove(self, model: type[Any], *criteria: ColumnElement[bool]) -> int:
        async with self._sm() as s:
            result = cast("CursorResult[Any]", await s.execute(delete(model).where(*criteria)))
            await s.commit()
            return result.rowcount

    async def drop_threads(self, thread_ids: list[str]) -> None:
        async with self._sm() as s:
            await checkpoints_repo.delete_threads(s, thread_ids)
            await s.commit()
