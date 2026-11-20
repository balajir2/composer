"""The async SQLAlchemy engine and its startup connect policy."""

import asyncio
import logging
import math

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import Settings
from src.db.url import to_sqlalchemy_url

logger = logging.getLogger(__name__)


def build_engine(settings: Settings, *, for_migrations: bool = False) -> AsyncEngine:
    """Create (but do not connect) the engine for ``settings.database_url``.

    ``prepare_threshold=None`` turns off server-side prepared statements,
    which Neon's transaction-mode pooler does not support. ``pool_pre_ping``
    checks a pooled connection before reuse, because Neon suspends idle
    compute and drops its connections.

    ``asyncio.timeout`` in ``connect_with_retry`` is the authoritative bound
    on a connect; libpq's ``connect_timeout`` is only a backstop.

    ``for_migrations=True`` is for one-shot runs (Alembic): connections are
    not pooled (``NullPool``), so the pool settings do not apply.
    """
    connect_args: dict[str, object] = {
        "prepare_threshold": None,
        # libpq takes whole seconds and treats anything below 2 as 2.
        "connect_timeout": max(2, math.ceil(settings.db_connect_timeout_seconds)),
    }
    url = to_sqlalchemy_url(settings.database_url)
    if for_migrations:
        return create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    return create_async_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


async def connect_with_retry(engine: AsyncEngine, settings: Settings) -> None:
    """Prove the database is reachable, retrying briefly for Neon cold starts.

    Mirrors Prisma's startup policy in ``src/storage/db.py``: a few fast
    attempts with doubling backoff, so a genuinely down database fails a
    boot in seconds instead of crash-looping (2026-08 incident).
    """
    max_attempts = settings.db_connect_max_attempts
    backoff = settings.db_connect_initial_backoff_seconds
    for attempt in range(1, max_attempts + 1):
        try:
            async with asyncio.timeout(settings.db_connect_timeout_seconds):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            if attempt > 1:
                logger.info("sqlalchemy: connected on attempt %d", attempt)
            return
        # OperationalError includes auth errors; they are retried harmlessly
        # within the tight budget, same as Prisma's policy.
        except (OperationalError, TimeoutError) as exc:
            if attempt == max_attempts:
                logger.error("sqlalchemy: giving up after %d attempts", max_attempts)
                raise
            logger.warning(
                "sqlalchemy: connect attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                max_attempts,
                type(exc).__name__,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
    # Unreachable while max_attempts >= 1 (enforced by Settings); defensive.
    raise RuntimeError("connect_with_retry made no attempts")
