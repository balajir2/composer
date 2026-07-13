"""Tests for the Postgres-backed atomic rate limiter (P1-4)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]

from src.security.rate_limit import BucketConfig
from src.security.rate_limit_pg import PostgresRateLimiter


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.ratelimitbucket = MagicMock()
    return db


async def test_first_request_creates_bucket_and_allows() -> None:
    db = _mock_db()
    db.ratelimitbucket.find_unique = AsyncMock(return_value=None)
    db.ratelimitbucket.create = AsyncMock()
    limiter = PostgresRateLimiter(db)
    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=0.5)
    )
    assert allowed is True
    assert retry_after == 0.0
    db.ratelimitbucket.create.assert_awaited_once()
    created = db.ratelimitbucket.create.await_args.kwargs["data"]
    assert created["tokens"] == 4.0  # capacity(5) - 1 consumed


async def test_exhausted_bucket_denies_with_retry_after() -> None:
    db = _mock_db()
    now = datetime.now(UTC)
    db.ratelimitbucket.find_unique = AsyncMock(return_value=MagicMock(tokens=0.0, lastRefill=now))
    limiter = PostgresRateLimiter(db)
    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=1.0)
    )
    assert allowed is False
    assert retry_after > 0


async def test_refill_over_elapsed_time_allows_again() -> None:
    db = _mock_db()
    stale = datetime.now(UTC) - timedelta(seconds=10)
    db.ratelimitbucket.find_unique = AsyncMock(return_value=MagicMock(tokens=0.0, lastRefill=stale))
    db.ratelimitbucket.update = AsyncMock()
    limiter = PostgresRateLimiter(db)
    # refill_per_second=1.0, 10s elapsed -> 10 tokens refilled, capped at capacity
    allowed, _ = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=1.0)
    )
    assert allowed is True
    updated = db.ratelimitbucket.update.await_args.kwargs["data"]
    assert updated["tokens"] == 4.0  # capped at capacity(5), minus 1 consumed


async def test_concurrent_first_requests_race_on_create_falls_back_to_refill() -> None:
    """Two concurrent first requests for the same key both see row=None; the
    loser's create() hits the (routeKey, clientKey) unique constraint. The
    loser must recover by re-reading the winner's row and running the normal
    refill/consume logic instead of raising or blindly returning allowed."""
    db = _mock_db()
    now = datetime.now(UTC)
    # Winner's row already consumed its one token (capacity=1), so the
    # loser must correctly be denied rather than assuming it still gets one.
    winner_row = MagicMock(tokens=0.0, lastRefill=now)
    db.ratelimitbucket.find_unique = AsyncMock(side_effect=[None, winner_row])
    db.ratelimitbucket.create = AsyncMock(
        side_effect=UniqueViolationError(
            {"user_facing_error": {"message": "Unique constraint failed"}}
        )
    )
    db.ratelimitbucket.update = AsyncMock()
    limiter = PostgresRateLimiter(db)

    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=1, refill_per_second=1.0)
    )

    assert allowed is False
    assert retry_after > 0
    assert db.ratelimitbucket.find_unique.await_count == 2
    db.ratelimitbucket.create.assert_awaited_once()
    db.ratelimitbucket.update.assert_not_awaited()
