"""Construction of the official ``AsyncPostgresSaver`` connections (pool builder, one-shot)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from src.checkpoints.schema import assert_saver_schema_current
from src.config import Settings
from src.db.url import to_libpq_url

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
