"""The official ``AsyncPostgresSaver``: pool builder, app lifespan, one-shot saver for the CLI.

The app's saver runs on its own ``psycopg_pool.AsyncConnectionPool`` (decision D4), separate from
SQLAlchemy's. The library checks a connection out of the pool for each saver call and returns it
when the call ends, so a graph run holds no checkpoint connection while a node runs.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

import psycopg
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from src.checkpoints.runtime import register_checkpointer, unregister_checkpointer
from src.checkpoints.schema import assert_saver_schema_current
from src.config import Settings, get_settings
from src.db.url import to_libpq_url

logger = logging.getLogger(__name__)

# autocommit + dict rows are what the saver requires; no prepared statements because Neon's
# pooler (pgbouncer, transaction mode) does not support them.
SAVER_CONNECTION_KWARGS: dict[str, Any] = {
    "autocommit": True,
    "row_factory": dict_row,
    "prepare_threshold": None,
}


def saver_conninfo(settings: Settings) -> str:
    if settings.checkpointer_use_direct_endpoint:
        raise NotImplementedError("direct endpoint conninfo arrives with phase 5 Task 8")
    return to_libpq_url(settings.database_url)


def build_saver_pool(settings: Settings) -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    """An unopened pool (``open=False``; the caller opens and closes it)."""
    pool = AsyncConnectionPool(
        conninfo=saver_conninfo(settings),
        min_size=settings.checkpointer_pool_min_size,
        max_size=settings.checkpointer_pool_max_size,
        timeout=settings.checkpointer_pool_timeout_seconds,
        max_idle=300,
        kwargs=SAVER_CONNECTION_KWARGS,
        check=AsyncConnectionPool.check_connection,  # Neon drops idle connections
        name="checkpointer",
        open=False,
    )
    return cast("AsyncConnectionPool[AsyncConnection[DictRow]]", pool)


@asynccontextmanager
async def open_one_shot_saver(settings: Settings) -> AsyncGenerator[AsyncPostgresSaver]:
    """One connection and a saver on it, for CLI commands; schema version checked first."""
    conn = cast(
        "AsyncConnection[DictRow]",
        await AsyncConnection.connect(saver_conninfo(settings), **SAVER_CONNECTION_KWARGS),
    )
    try:
        await assert_saver_schema_current(conn)
        yield AsyncPostgresSaver(conn)
    finally:
        await conn.close()


async def _close_pool(pool: AsyncConnectionPool[AsyncConnection[DictRow]]) -> None:
    """Close a pool; a failure is logged, never raised (shutdown and retries must go on)."""
    try:
        await pool.close()
    except Exception:
        logger.exception("checkpointer: closing the saver pool failed")


async def _open_pool_with_retry(
    settings: Settings,
) -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    """Open the saver pool with the same fail-fast policy as ``connect_with_retry``: a few
    attempts with doubling backoff, so a down database fails a boot in seconds (2026-08 incident)."""
    max_attempts = settings.db_connect_max_attempts
    backoff = settings.db_connect_initial_backoff_seconds
    for attempt in range(1, max_attempts + 1):
        pool = build_saver_pool(settings)
        try:
            await pool.open(wait=True, timeout=settings.db_connect_timeout_seconds)
        except (PoolTimeout, psycopg.OperationalError) as exc:
            await _close_pool(pool)
            if attempt == max_attempts:
                logger.error("checkpointer: giving up after %d attempts", max_attempts)
                raise
            logger.warning(
                "checkpointer: open attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                max_attempts,
                type(exc).__name__,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
        except BaseException:
            await _close_pool(pool)
            raise
        else:
            if attempt > 1:
                logger.info("checkpointer: pool opened on attempt %d", attempt)
            return pool
    raise RuntimeError("checkpointer pool open made no attempts")  # max_attempts >= 1


@asynccontextmanager
async def checkpointer_lifespan(app: FastAPI) -> AsyncGenerator[AsyncPostgresSaver]:
    """Open the saver pool, check the schema, attach the saver to ``app.state`` and register it.

    Fails closed: the app does not start when the pool cannot open or when the saver's tables are
    not at the version the installed library expects. On exit the saver is unregistered and
    detached, then the pool is closed, whatever else went wrong.
    """
    settings = get_settings()
    pool = await _open_pool_with_retry(settings)
    saver: AsyncPostgresSaver | None = None
    try:
        async with pool.connection() as conn:
            await assert_saver_schema_current(conn)
        saver = AsyncPostgresSaver(pool)
        app.state.checkpointer = saver
        register_checkpointer(saver)
        yield saver
    finally:
        if saver is not None:
            unregister_checkpointer(saver)
            if getattr(app.state, "checkpointer", None) is saver:
                delattr(app.state, "checkpointer")
        await _close_pool(pool)
