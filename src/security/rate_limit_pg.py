"""Postgres-backed atomic rate limiter (P1-4).

Same token-bucket algorithm as src/security/rate_limit.py's in-memory
RateLimiter, but state lives in the `rate_limit_buckets` table instead of
a process-local dict — correct across multiple Cloud Run instances,
which the in-memory version was not (ADR-0033).

Each check runs in one short transaction that reads the bucket with
``SELECT ... FOR UPDATE``, so concurrent checks of one bucket serialize and
exactly the available tokens are admitted. A first request inserts the row;
losing that race falls back to locking the winner's row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.db.errors import AlreadyExists
from src.db.repos import rate_limits as rate_limits_repo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.security.rate_limit import BucketConfig


class PostgresRateLimiter:
    """Drop-in replacement for RateLimiter with Postgres-backed state."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]:
        """Check + consume one token. Returns (allowed, retry_after_seconds)."""
        async with self.sessionmaker() as session:
            bucket = await rate_limits_repo.lock_bucket(session, route_key, client_key)
            if bucket is None:
                try:
                    await rate_limits_repo.insert_bucket(
                        session,
                        route_key,
                        client_key,
                        tokens=float(config.capacity) - 1.0,
                        last_refill=datetime.now(UTC),
                    )
                except AlreadyExists:
                    # Lost the race to a concurrent first request; its row may already
                    # have consumed the only token, so consume from the row that exists.
                    bucket = await rate_limits_repo.lock_bucket(session, route_key, client_key)
                    if bucket is None:
                        raise
                else:
                    await session.commit()
                    return True, 0.0

            now = datetime.now(UTC)
            elapsed = (now - bucket.last_refill).total_seconds()
            tokens = min(float(config.capacity), bucket.tokens + elapsed * config.refill_per_second)
            if tokens >= 1.0:
                await rate_limits_repo.save_bucket(
                    session, route_key, client_key, tokens=tokens - 1.0, last_refill=now
                )
                await session.commit()
                return True, 0.0

            # Deny without persisting: last_refill keeps its prior value so the next
            # check still accounts for the full elapsed window.
            await session.rollback()
            return False, (1.0 - tokens) / config.refill_per_second


__all__ = ["PostgresRateLimiter"]
