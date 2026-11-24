"""Rate-limit token buckets. Callers own the transaction."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import unique_violation_as_already_exists
from src.db.ids import new_id
from src.db.models import RateLimitBucket


@dataclass(frozen=True)
class BucketRecord:
    tokens: float
    last_refill: datetime


async def lock_bucket(
    session: AsyncSession, route_key: str, client_key: str
) -> BucketRecord | None:
    """Read the bucket with ``FOR UPDATE`` so concurrent checks of it serialize."""
    row = (
        await session.execute(
            select(RateLimitBucket.tokens, RateLimitBucket.last_refill)
            .where(RateLimitBucket.route_key == route_key, RateLimitBucket.client_key == client_key)
            .with_for_update()
        )
    ).first()
    return None if row is None else BucketRecord(row.tokens, row.last_refill)


async def insert_bucket(
    session: AsyncSession,
    route_key: str,
    client_key: str,
    *,
    tokens: float,
    last_refill: datetime,
) -> None:
    """Create the bucket; raises ``AlreadyExists`` (session stays usable) if it exists."""
    async with unique_violation_as_already_exists(session):
        session.add(
            RateLimitBucket(
                id=new_id(),
                route_key=route_key,
                client_key=client_key,
                tokens=tokens,
                last_refill=last_refill,
            )
        )
        await session.flush()


async def save_bucket(
    session: AsyncSession,
    route_key: str,
    client_key: str,
    *,
    tokens: float,
    last_refill: datetime,
) -> None:
    await session.execute(
        update(RateLimitBucket)
        .where(RateLimitBucket.route_key == route_key, RateLimitBucket.client_key == client_key)
        .values(tokens=tokens, last_refill=last_refill)
        .execution_options(synchronize_session=False)
    )
