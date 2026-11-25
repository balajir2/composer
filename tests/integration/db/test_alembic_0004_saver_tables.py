"""Revision 0004 creates the official saver's tables and marks its migrations applied
(real database, driven through the alembic CLI).

No upgrade lock-timeout case: the upgrade only creates new tables, so it has no existing
relation to wait on. The downgrade drops tables and does have one.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.url import to_libpq_url

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
PREV = "0003_assignment_user_fks"
TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _alembic(*args: str) -> None:
    done = _run_alembic(*args)
    assert done.returncode == 0, done.stdout + done.stderr


def _conn() -> psycopg.Connection:
    return psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)


def _existing_tables() -> set[str]:
    with _conn() as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
                (list(TABLES),),
            ).fetchall()
        }


async def test_tables_exist_and_all_saver_migrations_are_recorded(session: AsyncSession) -> None:
    for t in TABLES:
        assert await session.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}), t
    vs = (await session.execute(text("SELECT v FROM checkpoint_migrations ORDER BY v"))).scalars()
    assert list(vs) == list(range(10))


async def test_setup_is_a_no_op_after_alembic(test_database_url: str) -> None:
    connect: Any = AsyncConnection.connect
    async with await connect(
        to_libpq_url(test_database_url),
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=None,
    ) as conn:

        async def count() -> object:
            cur = await conn.execute("SELECT count(*) AS n FROM checkpoint_migrations")
            return await cur.fetchone()

        before = await count()
        await AsyncPostgresSaver(conn).setup()
        after = await count()
    assert before == after == {"n": 10}


def test_downgrade_and_upgrade_round_trip() -> None:
    try:
        _alembic("downgrade", PREV)
        assert _existing_tables() == set()
        _alembic("upgrade", "head")
        assert _existing_tables() == set(TABLES)
    finally:
        _alembic("upgrade", "head")


def test_downgrade_refuses_while_a_checkpoint_exists() -> None:
    _alembic("upgrade", "head")
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) "
                "VALUES ('t-0004', 'c1', '{}')"
            )
        failed = _run_alembic("downgrade", "-1")
        assert failed.returncode != 0
        assert "1 checkpoints exist" in failed.stdout + failed.stderr
        assert _existing_tables() == set(TABLES)  # nothing half-dropped
    finally:
        with _conn() as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = 't-0004'")
        _alembic("upgrade", "head")


def test_downgrade_fails_fast_when_a_saver_table_is_locked() -> None:
    blocker = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        _alembic("upgrade", "head")
        blocker.execute("LOCK TABLE checkpoint_blobs IN ROW EXCLUSIVE MODE")
        blocked = _run_alembic("downgrade", "-1")
        assert blocked.returncode != 0
        assert "lock timeout" in (blocked.stdout + blocked.stderr).lower()
        assert _existing_tables() == set(TABLES)
    finally:
        blocker.close()
        _alembic("upgrade", "head")
