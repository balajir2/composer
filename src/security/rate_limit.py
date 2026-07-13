"""Token-bucket rate limiter — in-memory per-key (Phase 8).

See ADR-0021 + Phase 8 spec §5.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request, status


@dataclass
class BucketConfig:
    capacity: int  # max tokens
    refill_per_second: float  # tokens added per second


class _RateLimiterProtocol(Protocol):
    async def check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]: ...


class TokenBucket:
    def __init__(self, config: BucketConfig) -> None:
        self.config = config
        self.tokens: float = float(config.capacity)
        self.last_refill: float = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(
            float(self.config.capacity),
            self.tokens + elapsed * self.config.refill_per_second,
        )
        self.last_refill = now

    def try_consume(self, cost: int = 1) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True, 0.0
        missing = cost - self.tokens
        retry = missing / self.config.refill_per_second
        return False, retry


class RateLimiter:
    """Per-key bucket registry. Key = (route, client_id) pair."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]:
        """Check + consume one token. Returns (allowed, retry_after_seconds)."""
        async with self._lock:
            bucket = self._buckets.get((route_key, client_key))
            if bucket is None:
                bucket = TokenBucket(config)
                self._buckets[(route_key, client_key)] = bucket
            return bucket.try_consume()


def get_rate_limiter(request: Request) -> _RateLimiterProtocol:
    """FastAPI dependency."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        raise RuntimeError("RateLimiter not attached to app.state")
    return limiter  # pyright: ignore[reportReturnType]


def per_minute_config(max_per_minute: int) -> BucketConfig:
    """A bucket that refills `max_per_minute` tokens/min."""
    return BucketConfig(
        capacity=max_per_minute,
        refill_per_second=max_per_minute / 60.0,
    )


async def enforce(
    limiter: _RateLimiterProtocol,
    route_key: str,
    client_key: str,
    config: BucketConfig,
) -> None:
    """Raise 429 with Retry-After header if bucket is empty."""
    allowed, retry_after = await limiter.check(route_key, client_key, config)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Retry after {int(retry_after) + 1} seconds.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


__all__ = [
    "BucketConfig",
    "RateLimiter",
    "TokenBucket",
    "_RateLimiterProtocol",
    "enforce",
    "get_rate_limiter",
    "per_minute_config",
]
