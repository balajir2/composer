"""Rate-limit bucket repository and the limiter's atomicity (decision D3)."""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.errors import AlreadyExists
from src.db.models import RateLimitBucket
from src.db.repos import rate_limits as repo
from src.security.rate_limit import BucketConfig
from src.security.rate_limit_pg import PostgresRateLimiter

pytestmark = pytest.mark.integration

T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2030, 1, 1, 12, 0, 5, tzinfo=UTC)


def _k() -> str:
    return f"k-{secrets.token_hex(5)}"


async def test_lock_insert_save_round_trip(session: AsyncSession) -> None:
    route, client = _k(), _k()
    assert await repo.lock_bucket(session, route, client) is None
    await repo.insert_bucket(session, route, client, tokens=4.0, last_refill=T0)
    assert await repo.lock_bucket(session, route, client) == repo.BucketRecord(4.0, T0)
    await repo.save_bucket(session, route, client, tokens=2.5, last_refill=T1)
    assert await repo.lock_bucket(session, route, client) == repo.BucketRecord(2.5, T1)


async def test_duplicate_insert_raises_already_exists_and_session_usable(
    session: AsyncSession,
) -> None:
    route, client = _k(), _k()
    await repo.insert_bucket(session, route, client, tokens=1.0, last_refill=T0)
    with pytest.raises(AlreadyExists):
        await repo.insert_bucket(session, route, client, tokens=9.0, last_refill=T1)
    got = await repo.lock_bucket(session, route, client)
    assert got is not None and got.tokens == 1.0


@pytest.fixture
async def sm(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield async_sessionmaker(engine, expire_on_commit=False)


async def _cleanup(sm: async_sessionmaker[AsyncSession], route: str) -> None:
    async with sm() as s:
        await s.execute(delete(RateLimitBucket).where(RateLimitBucket.route_key == route))
        await s.commit()


async def test_concurrent_checks_admit_exactly_the_tokens_available(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    limiter = PostgresRateLimiter(sm)
    cfg = BucketConfig(capacity=3, refill_per_second=1e-9)
    for _ in range(5):
        route, client = _k(), _k()
        try:
            async with sm() as s:
                await repo.insert_bucket(
                    s, route, client, tokens=3.0, last_refill=datetime.now(UTC)
                )
                await s.commit()
            results = await asyncio.gather(*(limiter.check(route, client, cfg) for _ in range(10)))
            assert sum(ok for ok, _ in results) == 3
        finally:
            await _cleanup(sm, route)


async def test_concurrent_first_checks_admit_exactly_capacity(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    limiter = PostgresRateLimiter(sm)
    cfg = BucketConfig(capacity=2, refill_per_second=1e-9)
    route, client = _k(), _k()
    try:
        results = await asyncio.gather(*(limiter.check(route, client, cfg) for _ in range(8)))
        assert sum(ok for ok, _ in results) == 2
    finally:
        await _cleanup(sm, route)


async def test_deny_leaves_last_refill_untouched(sm: async_sessionmaker[AsyncSession]) -> None:
    limiter = PostgresRateLimiter(sm)
    route, client = _k(), _k()
    stale = datetime.now(UTC)
    try:
        async with sm() as s:
            await repo.insert_bucket(s, route, client, tokens=0.0, last_refill=stale)
            await s.commit()
        ok, retry = await limiter.check(route, client, BucketConfig(1, 1e-9))
        assert ok is False and retry > 0
        async with sm() as s:
            row = (
                await s.execute(select(RateLimitBucket).where(RateLimitBucket.route_key == route))
            ).scalar_one()
        assert row.tokens == 0.0
        assert abs((row.last_refill - stale).total_seconds()) < 0.002
    finally:
        await _cleanup(sm, route)


async def test_check_denies_within_the_lock_timeout_when_the_bucket_is_held(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    limiter = PostgresRateLimiter(sm)
    route, client = _k(), _k()
    cfg = BucketConfig(capacity=3, refill_per_second=1e-9)
    stamp = datetime.now(UTC)
    try:
        async with sm() as s:
            await repo.insert_bucket(s, route, client, tokens=3.0, last_refill=stamp)
            await s.commit()
        async with sm() as holder:
            assert await repo.lock_bucket(holder, route, client) is not None  # row lock held
            started = time.monotonic()
            ok, retry = await limiter.check(route, client, cfg)
            waited = time.monotonic() - started
            await holder.rollback()
        assert ok is False and retry == 1.0
        assert 1.5 < waited < 4.0
        async with sm() as s:
            row = (
                await s.execute(select(RateLimitBucket).where(RateLimitBucket.route_key == route))
            ).scalar_one()
        assert row.tokens == 3.0  # the timed-out check consumed nothing
        ok_after, _ = await limiter.check(route, client, cfg)
        assert ok_after is True
    finally:
        await _cleanup(sm, route)
