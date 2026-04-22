"""Tests for RateLimiter + TokenBucket (Phase 8)."""

import time

import pytest
from fastapi import HTTPException

from src.security.rate_limit import (
    BucketConfig,
    RateLimiter,
    TokenBucket,
    enforce,
    per_minute_config,
)


def test_token_bucket_allows_under_capacity() -> None:
    bucket = TokenBucket(BucketConfig(capacity=5, refill_per_second=1.0))
    for _ in range(5):
        allowed, _ = bucket.try_consume()
        assert allowed


def test_token_bucket_denies_over_capacity() -> None:
    bucket = TokenBucket(BucketConfig(capacity=3, refill_per_second=0.1))
    for _ in range(3):
        bucket.try_consume()
    allowed, retry = bucket.try_consume()
    assert not allowed
    assert retry > 0


def test_token_bucket_refills_over_time() -> None:
    bucket = TokenBucket(BucketConfig(capacity=2, refill_per_second=100.0))
    bucket.try_consume()
    bucket.try_consume()
    time.sleep(0.05)  # 5 tokens worth at 100/s
    allowed, _ = bucket.try_consume()
    assert allowed


async def test_rate_limiter_per_key_isolation() -> None:
    limiter = RateLimiter()
    config = BucketConfig(capacity=2, refill_per_second=0.01)

    allowed, _ = await limiter.check("ep", "userA", config)
    assert allowed
    allowed, _ = await limiter.check("ep", "userA", config)
    assert allowed
    allowed, _ = await limiter.check("ep", "userA", config)
    assert not allowed

    allowed, _ = await limiter.check("ep", "userB", config)
    assert allowed


async def test_rate_limiter_route_isolation() -> None:
    limiter = RateLimiter()
    config = BucketConfig(capacity=1, refill_per_second=0.01)

    allowed, _ = await limiter.check("route_a", "u1", config)
    assert allowed
    allowed, _ = await limiter.check("route_b", "u1", config)
    assert allowed


async def test_enforce_raises_429_on_empty_bucket() -> None:
    limiter = RateLimiter()
    config = BucketConfig(capacity=1, refill_per_second=0.001)

    await enforce(limiter, "route", "u1", config)
    with pytest.raises(HTTPException) as exc_info:
        await enforce(limiter, "route", "u1", config)
    assert exc_info.value.status_code == 429
    headers = exc_info.value.headers
    assert headers is not None
    assert "Retry-After" in headers


def test_per_minute_config_derivation() -> None:
    config = per_minute_config(60)
    assert config.capacity == 60
    assert config.refill_per_second == pytest.approx(1.0)


async def test_enforce_under_capacity_passes() -> None:
    limiter = RateLimiter()
    config = BucketConfig(capacity=5, refill_per_second=1.0)
    # Should pass 5 times without raising
    for _ in range(5):
        await enforce(limiter, "route", "user", config)
