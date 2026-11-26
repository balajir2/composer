"""``checkpointer_lifespan`` with a fake pool: open-with-retry, schema check, clean shutdown."""

import asyncio
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from typing import ClassVar

import pytest
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import PoolTimeout

from src.checkpoints import runtime, saver
from src.config import get_settings


class FakePool:
    """Stands in for ``AsyncConnectionPool``. ``open_failures`` is how many opens fail first."""

    instances: ClassVar[list["FakePool"]] = []
    open_failures: ClassVar[int] = 0
    close_raises: ClassVar[bool] = False

    def __init__(self) -> None:
        self.opens = 0
        self.closed = 0
        FakePool.instances.append(self)

    async def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        self.opens += 1
        self.timeout = timeout
        if FakePool.open_failures > 0:
            FakePool.open_failures -= 1
            raise PoolTimeout("down")

    async def close(self) -> None:
        self.closed += 1
        if FakePool.close_raises:
            raise RuntimeError("close broke")

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[object]:
        yield object()


@pytest.fixture(autouse=True)
def _fake_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:  # pyright: ignore[reportUnusedFunction]
    FakePool.instances = []
    FakePool.open_failures = 0
    FakePool.close_raises = False
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def _schema_ok(_conn: object) -> None:
        return None

    def _build(_settings: object) -> FakePool:
        return FakePool()

    monkeypatch.setattr(saver, "build_saver_pool", _build)
    monkeypatch.setattr(saver, "assert_saver_schema_current", _schema_ok)
    monkeypatch.setattr(saver.asyncio, "sleep", _sleep)
    monkeypatch.setenv("DB_CONNECT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("DB_CONNECT_INITIAL_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "2")
    get_settings.cache_clear()
    runtime.register_checkpointer(None)
    yield sleeps
    get_settings.cache_clear()
    runtime.register_checkpointer(None)


async def test_lifespan_attaches_the_saver_registers_it_and_closes_the_pool() -> None:
    app = FastAPI()
    async with saver.checkpointer_lifespan(app) as cp:
        assert isinstance(cp, AsyncPostgresSaver)
        assert app.state.checkpointer is cp
        assert runtime.get_registered_checkpointer() is cp
        assert cp.conn is FakePool.instances[0]
        assert FakePool.instances[0].closed == 0
    assert FakePool.instances[0].closed == 1  # the pool is closed on exit
    assert not hasattr(app.state, "checkpointer")
    assert runtime.get_registered_checkpointer() is None


async def test_open_retries_with_doubling_backoff_then_succeeds(_fake_pool: list[float]) -> None:
    FakePool.open_failures = 2
    async with saver.checkpointer_lifespan(FastAPI()):
        pass
    assert len(FakePool.instances) == 3
    assert [p.closed for p in FakePool.instances] == [1, 1, 1]  # failed pools are closed too
    assert FakePool.instances[0].timeout == 2.0  # db_connect_timeout_seconds
    assert _fake_pool == [0.5, 1.0]


async def test_open_gives_up_after_max_attempts_and_raises() -> None:
    FakePool.open_failures = 99
    app = FastAPI()
    with pytest.raises(PoolTimeout):
        async with saver.checkpointer_lifespan(app):
            pytest.fail("the body must not run")
    assert len(FakePool.instances) == 3
    assert all(p.closed == 1 for p in FakePool.instances)
    assert not hasattr(app.state, "checkpointer")
    assert runtime.get_registered_checkpointer() is None


async def test_schema_mismatch_fails_closed_and_closes_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _bad(_conn: object) -> None:
        raise RuntimeError("checkpoint tables are at saver migration 8")

    monkeypatch.setattr(saver, "assert_saver_schema_current", _bad)
    app = FastAPI()
    with pytest.raises(RuntimeError, match="saver migration"):
        async with saver.checkpointer_lifespan(app):
            pytest.fail("the body must not run")
    assert FakePool.instances[0].closed == 1
    assert not hasattr(app.state, "checkpointer")
    assert runtime.get_registered_checkpointer() is None


async def test_shutdown_completes_when_the_pool_close_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    FakePool.close_raises = True
    app = FastAPI()
    with caplog.at_level("ERROR", logger="src.checkpoints.saver"):
        async with saver.checkpointer_lifespan(app):
            pass  # must not raise on exit
    assert not hasattr(app.state, "checkpointer")
    assert runtime.get_registered_checkpointer() is None
    assert any("closing the saver pool failed" in r.getMessage() for r in caplog.records)


async def test_a_body_error_still_closes_and_unregisters() -> None:
    app = FastAPI()
    with pytest.raises(ValueError, match="boom"):
        async with saver.checkpointer_lifespan(app):
            raise ValueError("boom")
    assert FakePool.instances[0].closed == 1
    assert runtime.get_registered_checkpointer() is None


async def test_unregister_leaves_a_newer_registration_in_place() -> None:
    """Lifespans can overlap (tests, a restart in one process): the older one's exit must not
    clear the newer one's registration."""
    older = AsyncPostgresSaver(FakePool())  # pyright: ignore[reportArgumentType]
    newer = AsyncPostgresSaver(FakePool())  # pyright: ignore[reportArgumentType]
    runtime.register_checkpointer(older)
    runtime.register_checkpointer(newer)
    runtime.unregister_checkpointer(older)
    assert runtime.get_registered_checkpointer() is newer
    runtime.unregister_checkpointer(newer)
    assert runtime.get_registered_checkpointer() is None


async def _forever(*_args: object) -> None:
    await asyncio.Event().wait()


async def test_a_schema_check_that_never_answers_times_out_and_closes_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-11-26 startup hang: the query on a silent connection waited forever."""
    monkeypatch.setenv("CHECKPOINTER_POOL_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    monkeypatch.setattr(saver, "assert_saver_schema_current", _forever)
    app = FastAPI()
    with pytest.raises(TimeoutError):
        async with saver.checkpointer_lifespan(app):
            pytest.fail("the body must not run")
    assert FakePool.instances[0].closed == 1
    assert runtime.get_registered_checkpointer() is None


async def test_a_checkout_that_never_returns_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pool's checkout timeout does not cover its liveness check; the lifespan bound does."""
    monkeypatch.setenv("CHECKPOINTER_POOL_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()

    @asynccontextmanager
    async def _stuck(_self: FakePool) -> AsyncGenerator[object]:
        await _forever()
        yield object()

    monkeypatch.setattr(FakePool, "connection", _stuck)
    with pytest.raises(TimeoutError):
        async with saver.checkpointer_lifespan(FastAPI()):
            pytest.fail("the body must not run")
    assert FakePool.instances[0].closed == 1
