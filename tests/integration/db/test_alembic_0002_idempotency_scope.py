"""Revision 0002 moves the idempotency-key uniqueness from (workflow, key) to
(workflow, user, key), and back (real database, driven through the alembic CLI)."""

import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
OLD = "workflow_executions_workflow_id_idempotency_key_key"
NEW = "workflow_executions_workflow_id_user_id_idempotency_key_key"


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


def _unique_indexes() -> dict[str, str]:
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'workflow_executions' AND indexdef LIKE 'CREATE UNIQUE%'"
        ).fetchall()
    return dict(rows)


def test_upgrade_and_downgrade_swap_the_unique_index() -> None:
    try:
        _alembic("upgrade", "head")
        at_head = _unique_indexes()
        assert NEW in at_head
        assert "(workflow_id, user_id, idempotency_key)" in at_head[NEW]
        assert "NULLS NOT DISTINCT" not in at_head[NEW]  # NULL keys must not collide
        assert OLD not in at_head

        _alembic("downgrade", "0001_baseline")
        reverted = _unique_indexes()
        assert OLD in reverted
        assert "(workflow_id, idempotency_key)" in reverted[OLD]
        assert NEW not in reverted

        _alembic("upgrade", "head")
        assert NEW in _unique_indexes()
        assert OLD not in _unique_indexes()
    finally:
        _alembic("upgrade", "head")


def test_upgrade_fails_fast_when_the_table_is_locked_then_succeeds_once_free() -> None:
    """A busy workflow_executions must fail the deploy quickly (lock_timeout), not
    queue every execution query behind the migration's SHARE lock."""
    blocker = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        _alembic("upgrade", "head")
        _alembic("downgrade", "0001_baseline")
        # ROW EXCLUSIVE conflicts with the SHARE lock CREATE UNIQUE INDEX needs.
        blocker.execute("LOCK TABLE workflow_executions IN ROW EXCLUSIVE MODE")
        started = time.monotonic()
        blocked = _run_alembic("upgrade", "head")
        elapsed = time.monotonic() - started
        assert blocked.returncode != 0, blocked.stdout + blocked.stderr
        assert "lock timeout" in (blocked.stdout + blocked.stderr).lower()
        assert elapsed < 25, f"upgrade took {elapsed:.1f}s to fail; lock_timeout not applied"
        assert OLD in _unique_indexes()
        assert NEW not in _unique_indexes()

        blocker.rollback()
        _alembic("upgrade", "head")
        assert NEW in _unique_indexes()
        assert OLD not in _unique_indexes()
    finally:
        blocker.close()
        _alembic("upgrade", "head")
