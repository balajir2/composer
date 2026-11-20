from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy.exc import OperationalError

from src.config import Settings
from src.db.engine import build_engine, connect_with_retry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost:5432/db?sslmode=disable",
        "db_connect_max_attempts": 3,
        "db_connect_timeout_seconds": 1.0,
        "db_connect_initial_backoff_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


async def test_build_engine_uses_psycopg_and_configured_pool() -> None:
    engine = build_engine(_settings(db_pool_size=4, db_max_overflow=1))
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.query["sslmode"] == "disable"
        assert engine.pool.size() == 4  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    finally:
        await engine.dispose()


class _FakeConnection:
    async def execute(self, _statement: object) -> None:
        return None


class _FlakyEngine:
    """Fails ``failures`` times with OperationalError, then connects."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[_FakeConnection]:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OperationalError("SELECT 1", {}, Exception("database is waking up"))
        yield _FakeConnection()


async def test_connect_with_retry_succeeds_after_transient_failures() -> None:
    engine = _FlakyEngine(failures=2)
    await connect_with_retry(cast("AsyncEngine", engine), _settings())
    assert engine.attempts == 3


async def test_connect_with_retry_gives_up_after_max_attempts() -> None:
    engine = _FlakyEngine(failures=10)
    with pytest.raises(OperationalError):
        await connect_with_retry(cast("AsyncEngine", engine), _settings())
    assert engine.attempts == 3
