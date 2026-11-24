"""Tests for the Postgres-backed atomic rate limiter (P1-4)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.db.errors import AlreadyExists
from src.db.repos import rate_limits as repo
from src.security.rate_limit import BucketConfig, enforce
from src.security.rate_limit_pg import PostgresRateLimiter
from tests.unit.db_fakes import FakeSessionmaker


class _Repo:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.lock = AsyncMock(return_value=None)
        self.insert = AsyncMock()
        self.save = AsyncMock()
        monkeypatch.setattr(repo, "lock_bucket", self.lock)
        monkeypatch.setattr(repo, "insert_bucket", self.insert)
        monkeypatch.setattr(repo, "save_bucket", self.save)


@pytest.fixture
def r(monkeypatch: pytest.MonkeyPatch) -> _Repo:
    return _Repo(monkeypatch)


def _limiter() -> tuple[PostgresRateLimiter, FakeSessionmaker]:
    sm = FakeSessionmaker()
    return PostgresRateLimiter(sm), sm  # pyright: ignore[reportArgumentType]


async def test_first_request_creates_bucket_and_allows(r: _Repo) -> None:
    limiter, sm = _limiter()
    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=0.5)
    )
    assert (allowed, retry_after) == (True, 0.0)
    r.insert.assert_awaited_once()
    assert r.insert.await_args is not None
    assert r.insert.await_args.kwargs["tokens"] == 4.0  # capacity(5) - 1 consumed
    sm.session.commit.assert_awaited_once()


async def test_exhausted_bucket_denies_with_retry_after_and_writes_nothing(r: _Repo) -> None:
    limiter, sm = _limiter()
    r.lock.return_value = repo.BucketRecord(0.0, datetime.now(UTC))
    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=1.0)
    )
    assert allowed is False
    assert retry_after > 0
    r.save.assert_not_awaited()
    sm.session.commit.assert_not_awaited()
    sm.session.rollback.assert_awaited_once()


async def test_refill_over_elapsed_time_allows_again(r: _Repo) -> None:
    limiter, sm = _limiter()
    r.lock.return_value = repo.BucketRecord(0.0, datetime.now(UTC) - timedelta(seconds=10))
    allowed, _ = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=1.0)
    )
    assert allowed is True
    assert r.save.await_args is not None
    assert r.save.await_args.kwargs["tokens"] == 4.0  # capped at capacity(5), minus 1 consumed
    sm.session.commit.assert_awaited_once()


async def test_lost_first_insert_race_consumes_from_the_winners_row(r: _Repo) -> None:
    """The loser must re-lock the winner's row and run the normal consume logic
    (here: the winner took the only token, so the loser is denied)."""
    limiter, _ = _limiter()
    r.lock.side_effect = [None, repo.BucketRecord(0.0, datetime.now(UTC))]
    r.insert.side_effect = AlreadyExists("rate_limit_buckets_route_key_client_key_key")

    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=1, refill_per_second=1.0)
    )

    assert allowed is False
    assert retry_after > 0
    assert r.lock.await_count == 2
    r.save.assert_not_awaited()


async def test_enforce_raises_429_when_denied(r: _Repo) -> None:
    limiter, _ = _limiter()
    r.lock.return_value = repo.BucketRecord(0.0, datetime.now(UTC))
    with pytest.raises(HTTPException) as exc_info:
        await enforce(
            limiter,
            route_key="executions",
            client_key="u1",
            config=BucketConfig(capacity=1, refill_per_second=0.01),
        )
    assert exc_info.value.status_code == 429
