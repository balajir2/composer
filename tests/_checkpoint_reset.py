"""Shared helper that removes the checkpoint rows a test created, in all five checkpoint tables.

Graph runs write the official saver's tables (``checkpoints``, ``checkpoint_blobs``,
``checkpoint_writes``) and, in tests that seed the old saver, ``langgraph_checkpoints`` and
``langgraph_checkpoint_writes``. Many tests clean up only their own execution rows, or only the
old tables (written before phase 5), so saver rows pile up across the session. The Alembic 0004
downgrade refuses while the saver tables hold rows, so a whole-suite run in one database failed
the 0002/0003/0004 up/down tests. This snapshots the thread ids present before a test and deletes
every other thread afterwards.

Like ``tests/_rate_limit_reset.py`` it takes a bare connection and does not depend on the ``app``
fixture, because some modules override fixtures that depend on ``app`` (see
``test_events_ws_hardening.py``).
"""

import os
from typing import Any

import psycopg
from psycopg import sql

from src.db.url import to_libpq_url

_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "langgraph_checkpoint_writes",
    "langgraph_checkpoints",
)


_SAVER_TABLES = _TABLES[:3]
_LEGACY_TABLES = _TABLES[3:]


async def _connect() -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(
        to_libpq_url(os.environ["TEST_DATABASE_URL"]), autocommit=True, prepare_threshold=None
    )


async def _exists(conn: psycopg.AsyncConnection[Any], table: str) -> bool:
    row = await (await conn.execute("SELECT to_regclass(%s)", (table,))).fetchone()
    return row is not None and row[0] is not None


async def _threads_in(
    conn: psycopg.AsyncConnection[Any], tables: tuple[str, ...]
) -> set[str] | None:
    """Thread ids in the table set, or None when its tables do not exist (an Alembic test may
    have downgraded them away)."""
    if not await _exists(conn, tables[0]):
        return None
    query = sql.SQL(" UNION ").join(
        sql.SQL("SELECT thread_id FROM {}").format(sql.Identifier(t)) for t in tables
    )
    cur = await conn.execute(query)
    return {str(r[0]) for r in await cur.fetchall()}


async def checkpoint_thread_ids() -> dict[str, set[str] | None] | None:
    """Per table set ("saver", "legacy"), the thread ids present before the test (None when that
    set's tables are absent), or None overall when there is no test database."""
    if not os.environ.get("TEST_DATABASE_URL"):
        return None
    async with await _connect() as conn:
        return {
            "saver": await _threads_in(conn, _SAVER_TABLES),
            "legacy": await _threads_in(conn, _LEGACY_TABLES),
        }


async def delete_checkpoint_threads_except(keep: dict[str, set[str] | None]) -> None:
    """Delete every checkpoint row whose thread was not present in that table set at snapshot
    time. A set that did not exist then is left alone, so its pre-existing rows are never swept."""
    if not os.environ.get("TEST_DATABASE_URL"):
        return
    async with await _connect() as conn:
        for name, tables in (("saver", _SAVER_TABLES), ("legacy", _LEGACY_TABLES)):
            kept = keep.get(name)
            if kept is None:
                continue
            for table in tables:
                if not await _exists(conn, table):
                    continue
                await conn.execute(
                    sql.SQL("DELETE FROM {} WHERE thread_id <> ALL(%s)").format(
                        sql.Identifier(table)
                    ),
                    (list(kept),),
                )
