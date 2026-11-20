from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import psycopg.errors
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists, add_or_already_exists


class _Diag:
    constraint_name = "workflows_slug_key"


class _FakeUniqueViolation(psycopg.errors.UniqueViolation):
    @property
    def diag(self) -> Any:  # pyright: ignore[reportIncompatibleVariableOverride]
        return _Diag()


class _FakeSession:
    def __init__(self, error: Exception | None = None) -> None:
        self.flush = AsyncMock(side_effect=error)
        self.add_all = MagicMock()
        self.nested_entered = 0

    @asynccontextmanager
    async def begin_nested(self) -> AsyncGenerator[None]:
        self.nested_entered += 1
        yield


def _as_session(fake: _FakeSession) -> AsyncSession:
    return cast("AsyncSession", fake)


async def test_unique_violation_becomes_already_exists_with_constraint() -> None:
    err = IntegrityError("INSERT", {}, _FakeUniqueViolation("dup"))
    session = _FakeSession(err)
    with pytest.raises(AlreadyExists) as info:
        await add_or_already_exists(_as_session(session), object())
    assert info.value.constraint == "workflows_slug_key"


async def test_other_integrity_errors_propagate_unchanged() -> None:
    err = IntegrityError("INSERT", {}, psycopg.errors.ForeignKeyViolation("fk"))
    session = _FakeSession(err)
    with pytest.raises(IntegrityError) as info:
        await add_or_already_exists(_as_session(session), object())
    assert info.value is err


async def test_successful_flush_returns_none() -> None:
    session = _FakeSession()
    assert await add_or_already_exists(_as_session(session), object()) is None
    session.flush.assert_awaited_once()
    session.add_all.assert_called_once()
    assert session.nested_entered == 1
