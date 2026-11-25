"""Postgres-backed atomic rate limiter (P1-4).

Same token-bucket algorithm as src/security/rate_limit.py's in-memory
RateLimiter, but state lives in the `rate_limit_buckets` table instead of
a process-local dict — correct across multiple Cloud Run instances,
which the in-memory version was not (ADR-0033).

Each check runs in one short transaction that reads the bucket with
``SELECT ... FOR UPDATE``, so concurrent checks of one bucket serialize and
exactly the available tokens are admitted. A first request inserts the row;
losing that race falls back to locking the winner's row.

The transaction starts with ``SET LOCAL lock_timeout = '2s'`` so a stuck bucket
lock cannot pin a pool connection (and the request) indefinitely; a lock timeout
is a deny (``LOCK_TIMEOUT_RETRY_AFTER`` seconds) that consumes no token.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.db.errors import AlreadyExists
from src.db.repos import rate_limits as rate_limits_repo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.security.rate_limit import BucketConfig


logger = logging.getLogger(__name__)

LOCK_TIMEOUT_RETRY_AFTER = 1.0
_LOCK_NOT_AVAILABLE = "55P03"


def _is_lock_timeout(exc: OperationalError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE


class PostgresRateLimiter:
    """Drop-in replacement for RateLimiter with Postgres-backed state."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]:
        """Check + consume one token. Returns (allowed, retry_after_seconds)."""
        try:
            return await self._check(route_key, client_key, config)
        except OperationalError as exc:
            if not _is_lock_timeout(exc):
                raise
            # Another request holds this bucket's row lock past the timeout. Deny rather
            # than 500; nothing was written, so no token is consumed. Log the route only.
            logger.warning("rate-limit bucket lock timed out for route %s", route_key)
            return False, LOCK_TIMEOUT_RETRY_AFTER

    async def _check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]:
        async with self.sessionmaker() as session:
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
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
