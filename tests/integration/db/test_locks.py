import asyncio
import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.db.locks import LockTimeoutError, acquire_xact_lock

pytestmark = pytest.mark.integration


async def test_second_holder_times_out_until_the_first_transaction_ends(
    engine: AsyncEngine,
) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    key = "test_lock:" + secrets.token_hex(6)
    async with sm() as first:
        await acquire_xact_lock(first, key)
        async with sm() as second:
            with pytest.raises(LockTimeoutError, match="timed out"):
                await acquire_xact_lock(second, key, timeout="300ms")
        # a different key is independent
        async with sm() as other:
            await acquire_xact_lock(other, key + "-other", timeout="300ms")
        await first.commit()  # ends the transaction, releasing the lock
    async with sm() as third:
        await acquire_xact_lock(third, key, timeout="300ms")


async def test_waiter_gets_the_lock_when_the_holder_commits(engine: AsyncEngine) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    key = "test_lock:" + secrets.token_hex(6)
    order: list[str] = []

    async def holder() -> None:
        async with sm() as s:
            await acquire_xact_lock(s, key)
            order.append("holder-locked")
            await asyncio.sleep(0.5)
            order.append("holder-commit")
            await s.commit()

    async def waiter() -> None:
        await asyncio.sleep(0.1)
        async with sm() as s:
            await acquire_xact_lock(s, key, timeout="5s")
            order.append("waiter-locked")
            await s.commit()

    await asyncio.gather(holder(), waiter())
    assert order == ["holder-locked", "holder-commit", "waiter-locked"]
