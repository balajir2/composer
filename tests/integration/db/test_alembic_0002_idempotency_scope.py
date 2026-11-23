"""Revision 0002 moves the idempotency-key uniqueness from (workflow, key) to
(workflow, user, key), and back (real database, driven through the alembic CLI)."""

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
OLD = "workflow_executions_workflow_id_idempotency_key_key"
NEW = "workflow_executions_workflow_id_user_id_idempotency_key_key"


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
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
        assert OLD not in at_head

        _alembic("downgrade", "-1")
        reverted = _unique_indexes()
        assert OLD in reverted
        assert "(workflow_id, idempotency_key)" in reverted[OLD]
        assert NEW not in reverted

        _alembic("upgrade", "head")
        assert NEW in _unique_indexes()
        assert OLD not in _unique_indexes()
    finally:
        _alembic("upgrade", "head")
