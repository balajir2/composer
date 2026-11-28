"""Shared helper that removes the checkpoint rows a test created, in the three saver tables.

Graph runs write the official saver's tables (``checkpoints``, ``checkpoint_blobs``,
``checkpoint_writes``). Many tests clean up only their own execution rows, so saver rows pile up
across the session. The Alembic 0004 downgrade refuses while the saver tables hold rows, so a
whole-suite run in one database failed the 0002/0003/0004 up/down tests. This snapshots the
thread ids present before a test and deletes every other thread afterwards. A test that
downgraded the saver tables away (0004) leaves them absent, hence the ``_exists`` checks.

Like ``tests/_rate_limit_reset.py`` it takes a bare connection and does not depend on the ``app``
fixture, because some modules override fixtures that depend on ``app`` (see
``test_events_ws_hardening.py``).
"""

import os
from typing import Any

import psycopg
from psycopg import sql

from src.db.url import to_libpq_url

_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


async def _connect() -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(
        to_libpq_url(os.environ["TEST_DATABASE_URL"]), autocommit=True, prepare_threshold=None
    )


async def _exists(conn: psycopg.AsyncConnection[Any], table: str) -> bool:
    row = await (await conn.execute("SELECT to_regclass(%s)", (table,))).fetchone()
    return row is not None and row[0] is not None


async def checkpoint_thread_ids() -> set[str] | None:
    """The thread ids present before the test; None when there is no test database or the saver
    tables are absent (an Alembic test may have downgraded them away)."""
    if not os.environ.get("TEST_DATABASE_URL"):
        return None
    async with await _connect() as conn:
        for table in _TABLES:
            if not await _exists(conn, table):
                return None
        query = sql.SQL(" UNION ").join(
            sql.SQL("SELECT thread_id FROM {}").format(sql.Identifier(t)) for t in _TABLES
        )
        cur = await conn.execute(query)
        return {str(r[0]) for r in await cur.fetchall()}


async def delete_checkpoint_threads_except(keep: set[str] | None) -> None:
    """Delete every checkpoint row whose thread was not present at snapshot time. With no
    snapshot (None) nothing is swept, so pre-existing rows are never deleted."""
    if keep is None or not os.environ.get("TEST_DATABASE_URL"):
        return
    async with await _connect() as conn:
        for table in _TABLES:
            if not await _exists(conn, table):
                continue
            await conn.execute(
                sql.SQL("DELETE FROM {} WHERE thread_id <> ALL(%s)").format(sql.Identifier(table)),
                (list(keep),),
            )
