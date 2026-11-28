"""Shared helper that removes the ``execution_attempts`` rows a test left behind.

Many integration tests create executions (and so attempt rows) and never delete them. The Alembic
0005 downgrade refuses, on purpose, while any attempt row exists, so a second whole-suite run in
one database failed every up/down migration test that passes through 0005. This snapshots the
attempt ids present before a test and, afterwards, deletes every other attempt and clears the
pending-approval pointer on executions that no longer have one. Rows that existed before the test
are never touched, and 0005's refusal itself is unchanged.

Like ``tests/_checkpoint_reset.py`` it takes a bare connection and does not depend on the ``app``
fixture.
"""

import os
from typing import Any

import psycopg

from src.db.url import to_libpq_url


async def _connect() -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(
        to_libpq_url(os.environ["TEST_DATABASE_URL"]), autocommit=True, prepare_threshold=None
    )


async def _exists(conn: psycopg.AsyncConnection[Any], table: str) -> bool:
    row = await (await conn.execute("SELECT to_regclass(%s)", (table,))).fetchone()
    return row is not None and row[0] is not None


async def attempt_ids() -> set[str] | None:
    """The attempt ids present before the test; None when there is no test database or the table
    is absent (an Alembic test may have downgraded it away)."""
    if not os.environ.get("TEST_DATABASE_URL"):
        return None
    async with await _connect() as conn:
        if not await _exists(conn, "execution_attempts"):
            return None
        cur = await conn.execute("SELECT id FROM execution_attempts")
        return {str(r[0]) for r in await cur.fetchall()}


async def delete_attempts_except(keep: set[str] | None) -> None:
    """Delete every attempt that was not present at snapshot time. With no snapshot (None)
    nothing is swept."""
    if keep is None or not os.environ.get("TEST_DATABASE_URL"):
        return
    async with await _connect() as conn:
        if not await _exists(conn, "execution_attempts"):
            return
        await conn.execute("DELETE FROM execution_attempts WHERE id <> ALL(%s)", (list(keep),))
        if keep:
            return
        # Nothing existed before the test, so no pending pointer can belong to a kept attempt.
        await conn.execute(
            "UPDATE workflow_executions SET pending_attempt_approval_id = NULL "
            "WHERE pending_attempt_approval_id IS NOT NULL"
        )
