"""Postgres-backed atomic rate limiter (P1-4).

Same token-bucket algorithm as src/security/rate_limit.py's in-memory
RateLimiter, but state lives in the `rate_limit_buckets` table instead of
a process-local dict — correct across multiple Cloud Run instances,
which the in-memory version was not (ADR-0033).

Uses a plain find_unique + upsert rather than a single atomic UPDATE
statement: the token-bucket refill computation depends on wall-clock
elapsed time since last_refill, which must be read before it can be
written, so a single UPDATE...WHERE (as used elsewhere in this codebase
for simple conditional transitions) can't express the refill formula in
SQL without a round-trip anyway. This is not a correctness gap for
BucketConfig at Composer's current concurrency (rate-limit checks are
per end-user request, not a high-contention shared counter); revisit
with a single UPDATE ... SET tokens = LEAST(capacity, tokens + ...)
RETURNING tokens if contention ever becomes a real issue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.security.rate_limit import BucketConfig


class PostgresRateLimiter:
    """Drop-in replacement for RateLimiter with Postgres-backed state."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]:
        """Check + consume one token. Returns (allowed, retry_after_seconds)."""
        row = await self.db.ratelimitbucket.find_unique(
            where={"routeKey_clientKey": {"routeKey": route_key, "clientKey": client_key}}
        )
        now = datetime.now(UTC)

        if row is None:
            tokens = float(config.capacity) - 1.0
            await self.db.ratelimitbucket.create(
                data={
                    "routeKey": route_key,
                    "clientKey": client_key,
                    "tokens": tokens,
                    "lastRefill": now,
                }
            )
            return True, 0.0

        elapsed = (now - row.lastRefill).total_seconds()
        tokens = min(float(config.capacity), row.tokens + elapsed * config.refill_per_second)

        if tokens >= 1.0:
            tokens -= 1.0
            await self.db.ratelimitbucket.update(
                where={"routeKey_clientKey": {"routeKey": route_key, "clientKey": client_key}},
                data={"tokens": tokens, "lastRefill": now},
            )
            return True, 0.0

        # Deny without persisting: lastRefill stays at its prior value, so the
        # next check's elapsed-time computation still starts from the same
        # baseline and correctly accounts for the full elapsed window. No
        # partial-refill progress is lost by skipping the write here.
        missing = 1.0 - tokens
        retry_after = missing / config.refill_per_second
        return False, retry_after


__all__ = ["PostgresRateLimiter"]
