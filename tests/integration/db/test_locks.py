import asyncio
import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db import locks
from src.db.locks import LockTimeoutError, run_under_lock, try_acquire_xact_lock

pytestmark = pytest.mark.integration


def _key() -> str:
    return "test_lock:" + secrets.token_hex(6)


async def test_try_lock_is_refused_while_another_transaction_holds_it(
    engine: AsyncEngine,
) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    key = _key()
    async with sm() as first, sm() as second:
        assert await try_acquire_xact_lock(first, key) is True
        assert await try_acquire_xact_lock(second, key) is False  # does not block
        await second.rollback()
        assert await try_acquire_xact_lock(second, key + "-other") is True  # independent keys
        await first.commit()  # ends the transaction, releasing the lock
    async with sm() as third:
        assert await try_acquire_xact_lock(third, key) is True


async def test_run_under_lock_refused_caller_waits_without_running_its_work_until_the_winner_is_done(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locks, "POLL_MIN_S", 0.01)
    monkeypatch.setattr(locks, "POLL_MAX_S", 0.02)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    key = _key()
    results: list[str] = []

    async def winner_work(session: AsyncSession) -> str:
        results.append("winner-working")
        await asyncio.sleep(0.3)
        await session.commit()
        results.append("winner-done")
        return "winner"

    async def second_work(session: AsyncSession) -> str:
        # only runs once the winner has released the lock (committed)
        assert "winner-done" in results
        await session.commit()
        return "winner"

    async def check() -> str | None:
        return "winner" if "winner-done" in results else None

    first = asyncio.create_task(run_under_lock(sm, key, winner_work, wait_s=5))
    await asyncio.sleep(0.1)  # the winner holds the lock now
    second = await run_under_lock(sm, key, second_work, wait_s=5, check=check)
    assert second == "winner" and await first == "winner"


async def test_run_under_lock_deadline(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locks, "POLL_MIN_S", 0.01)
    monkeypatch.setattr(locks, "POLL_MAX_S", 0.02)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    key = _key()

    async def never(_session: AsyncSession) -> str:
        raise AssertionError("not granted")

    async with sm() as holder:
        assert await try_acquire_xact_lock(holder, key)
        with pytest.raises(LockTimeoutError, match="not granted within"):
            await run_under_lock(sm, key, never, wait_s=0.2)
