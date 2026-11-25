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

import asyncpg

_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "langgraph_checkpoint_writes",
    "langgraph_checkpoints",
)

_THREADS_SQL = " UNION ".join(f"SELECT thread_id FROM {t}" for t in _TABLES)


async def checkpoint_thread_ids() -> set[str] | None:
    """Thread ids present in any checkpoint table, or None when there is no test database."""
    if not os.environ.get("TEST_DATABASE_URL"):
        return None
    conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        if await conn.fetchval("SELECT to_regclass('checkpoints')") is None:
            return set()  # a test (Alembic downgrade) may leave the saver tables absent
        return {r["thread_id"] for r in await conn.fetch(_THREADS_SQL)}
    finally:
        await conn.close()


async def delete_checkpoint_threads_except(keep: set[str]) -> None:
    """Delete every checkpoint row whose thread is not in ``keep``."""
    if not os.environ.get("TEST_DATABASE_URL"):
        return
    conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        for table in _TABLES:
            if await conn.fetchval("SELECT to_regclass($1)", table) is None:
                continue
            await conn.execute(
                f"DELETE FROM {table} WHERE thread_id <> ALL($1::text[])", list(keep)
            )
    finally:
        await conn.close()
