import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

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


async def test_build_engine_for_migrations_uses_one_shot_connections() -> None:
    engine = build_engine(_settings(db_pool_size=4, db_max_overflow=1), for_migrations=True)
    try:
        assert isinstance(engine.sync_engine.pool, NullPool)
        assert engine.url.drivername == "postgresql+psycopg"
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


async def test_connect_with_retry_logs_when_giving_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _FlakyEngine(failures=10)
    with caplog.at_level(logging.ERROR, logger="src.db.engine"), pytest.raises(OperationalError):
        await connect_with_retry(cast("AsyncEngine", engine), _settings())
    assert "giving up after 3 attempts" in caplog.text


class _HangingEngine:
    """connect() never yields within the timeout."""

    def __init__(self) -> None:
        self.attempts = 0

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[_FakeConnection]:
        self.attempts += 1
        await asyncio.sleep(10)
        yield _FakeConnection()


async def test_connect_with_retry_times_out_hung_connects() -> None:
    engine = _HangingEngine()
    settings = _settings(db_connect_timeout_seconds=0.05)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await connect_with_retry(cast("AsyncEngine", engine), settings)
    assert engine.attempts == 3
    assert time.monotonic() - started < 2


async def test_connect_with_retry_doubles_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("src.db.engine.asyncio.sleep", record_sleep)
    engine = _FlakyEngine(failures=10)
    settings = _settings(db_connect_initial_backoff_seconds=0.5)
    with pytest.raises(OperationalError):
        await connect_with_retry(cast("AsyncEngine", engine), settings)
    assert delays == [0.5, 1.0]


def test_build_engine_passes_connect_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create(url: object, **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("src.db.engine.create_async_engine", fake_create)
    build_engine(_settings(db_connect_timeout_seconds=3.0))
    assert captured["connect_args"] == {"prepare_threshold": None, "connect_timeout": 3}
