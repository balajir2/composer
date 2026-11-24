"""Revision 0003 adds foreign keys from workflow_assignments to users (real database,
driven through the alembic CLI)."""

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from psycopg import sql

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
USER_FK = "workflow_assignments_user_id_fkey"
BY_FK = "workflow_assignments_assigned_by_id_fkey"
BY_INDEX = "workflow_assignments_assigned_by_id_idx"
PREV = "0002_idempotency_per_user"


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


def _constraints() -> dict[str, str]:
    with _conn() as conn:
        return dict(
            conn.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'workflow_assignments'::regclass AND contype = 'f'"
            ).fetchall()
        )


def _indexes() -> set[str]:
    with _conn() as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'workflow_assignments'"
            ).fetchall()
        }


def _assigned_by_nullable() -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'workflow_assignments' AND column_name = 'assigned_by_id'"
        ).fetchone()
    assert row is not None
    return row[0] == "YES"


def _tag() -> str:
    return secrets.token_hex(4)


def _seed_user(conn: psycopg.Connection, uid: str) -> None:
    conn.execute(
        "INSERT INTO users (id, email, updated_at) VALUES (%s, %s, now())",
        (uid, f"{uid}@example.com"),
    )


def _seed_workflow(conn: psycopg.Connection, wid: str) -> None:
    conn.execute(
        "INSERT INTO workflows (id, name, nodes, edges, updated_at) "
        "VALUES (%s, 'w', '[]', '[]', now())",
        (wid,),
    )


def _seed_assignment(
    conn: psycopg.Connection, aid: str, wid: str, user_id: str, by: str | None
) -> None:
    conn.execute(
        "INSERT INTO workflow_assignments (id, workflow_id, user_id, assigned_by_id) "
        "VALUES (%s, %s, %s, %s)",
        (aid, wid, user_id, by),
    )


def _assignment(aid: str) -> tuple[str, str | None] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT user_id, assigned_by_id FROM workflow_assignments WHERE id = %s", (aid,)
        ).fetchone()
    return None if row is None else (row[0], row[1])


def test_upgrade_cleans_orphans_adds_fks_and_downgrade_restores() -> None:
    t = _tag()
    wid, ok_user, ok_by = f"w-{t}", f"u-{t}", f"g-{t}"
    ghost_user, ghost_by = f"ghost-u-{t}", f"ghost-g-{t}"
    a_valid, a_orphan, a_nogranter = f"a1-{t}", f"a2-{t}", f"a3-{t}"
    try:
        _alembic("downgrade", PREV)
        with _conn() as conn:
            _seed_user(conn, ok_user)
            _seed_user(conn, ok_by)
            _seed_workflow(conn, wid)
            _seed_assignment(conn, a_valid, wid, ok_user, ok_by)
            _seed_assignment(conn, a_orphan, wid, ghost_user, ok_by)
            _seed_assignment(conn, a_nogranter, wid, ok_by, ghost_by)

        _alembic("upgrade", "head")

        assert _assignment(a_valid) == (ok_user, ok_by)
        assert _assignment(a_orphan) is None
        assert _assignment(a_nogranter) == (ok_by, None)
        fks = _constraints()
        assert "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in fks[USER_FK]
        assert "FOREIGN KEY (assigned_by_id) REFERENCES users(id) ON DELETE SET NULL" in fks[BY_FK]
        with _conn() as conn:  # both validated, not left NOT VALID
            rows = conn.execute(
                "SELECT conname, convalidated FROM pg_constraint WHERE conname IN (%s, %s)",
                (USER_FK, BY_FK),
            ).fetchall()
        assert dict(rows) == {USER_FK: True, BY_FK: True}
        assert _assigned_by_nullable()
        assert BY_INDEX in _indexes()
        assert "workflow_assignments_user_id_idx" in _indexes()

        # Deleting the granter keeps the row with a NULL granter; deleting the assignee
        # cascades.
        with _conn() as conn:
            conn.execute("DELETE FROM users WHERE id = %s", (ok_by,))
        assert _assignment(a_valid) == (ok_user, None)
        assert _assignment(a_nogranter) is None  # ok_by was its assignee
        with _conn() as conn:
            conn.execute("DELETE FROM users WHERE id = %s", (ok_user,))
        assert _assignment(a_valid) is None

        # A grant to a user that does not exist is now rejected by the database.
        with _conn() as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
            _seed_assignment(conn, f"a4-{t}", wid, ghost_user, None)

        # Downgrade (no NULL granters left to violate NOT NULL) restores the old shape.
        with _conn() as conn:
            conn.execute("DELETE FROM workflow_assignments WHERE assigned_by_id IS NULL")
        _alembic("downgrade", "-1")
        assert _constraints().keys() == {"workflow_assignments_workflow_id_fkey"}
        assert not _assigned_by_nullable()
        assert BY_INDEX not in _indexes()
        assert "workflow_assignments_user_id_idx" in _indexes()

        _alembic("upgrade", "head")
        assert {USER_FK, BY_FK} <= _constraints().keys()
    finally:
        _alembic("upgrade", "head")
        with _conn() as conn:
            conn.execute("DELETE FROM workflows WHERE id = %s", (wid,))
            conn.execute("DELETE FROM users WHERE id IN (%s, %s)", (ok_user, ok_by))


def test_downgrade_fails_honestly_when_a_granter_is_null() -> None:
    t = _tag()
    wid, uid = f"w-{t}", f"u-{t}"
    try:
        _alembic("upgrade", "head")
        with _conn() as conn:
            _seed_user(conn, uid)
            _seed_workflow(conn, wid)
            _seed_assignment(conn, f"a-{t}", wid, uid, None)
        failed = _run_alembic("downgrade", "-1")
        assert failed.returncode != 0
        # Nothing was half-applied: still at head with the FKs.
        assert {USER_FK, BY_FK} <= _constraints().keys()
        assert _assigned_by_nullable()
    finally:
        with _conn() as conn:
            conn.execute("DELETE FROM workflows WHERE id = %s", (wid,))
            conn.execute("DELETE FROM users WHERE id = %s", (uid,))
        _alembic("upgrade", "head")


@pytest.mark.parametrize(
    ("table", "mode"),
    [
        ("workflow_assignments", "ROW EXCLUSIVE"),  # a writer on the assignments table
        (
            "workflow_assignments",
            "ACCESS SHARE",
        ),  # even a reader: the migration needs ACCESS EXCLUSIVE
        ("users", "ROW EXCLUSIVE"),  # a user write conflicts with the lock on users
    ],
)
def test_upgrade_fails_fast_when_a_table_is_locked_then_succeeds_once_free(
    table: str, mode: LiteralString
) -> None:
    """A busy workflow_assignments or users must fail the deploy quickly (lock_timeout)."""
    blocker = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        _alembic("upgrade", "head")
        _alembic("downgrade", PREV)
        blocker.execute(
            sql.SQL("LOCK TABLE {} IN {} MODE").format(sql.Identifier(table), sql.SQL(mode))
        )
        started = time.monotonic()
        blocked = _run_alembic("upgrade", "head")
        elapsed = time.monotonic() - started
        assert blocked.returncode != 0, blocked.stdout + blocked.stderr
        assert "lock timeout" in (blocked.stdout + blocked.stderr).lower()
        assert elapsed < 25, f"upgrade took {elapsed:.1f}s to fail; lock_timeout not applied"
        assert USER_FK not in _constraints()

        blocker.rollback()
        _alembic("upgrade", "head")
        assert {USER_FK, BY_FK} <= _constraints().keys()
    finally:
        blocker.close()
        _alembic("upgrade", "head")


def test_downgrade_fails_fast_when_users_is_locked() -> None:
    blocker = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        _alembic("upgrade", "head")
        blocker.execute("LOCK TABLE users IN ROW EXCLUSIVE MODE")
        blocked = _run_alembic("downgrade", "-1")
        assert blocked.returncode != 0
        assert "lock timeout" in (blocked.stdout + blocked.stderr).lower()
        assert {USER_FK, BY_FK} <= _constraints().keys()
    finally:
        blocker.close()
        _alembic("upgrade", "head")
