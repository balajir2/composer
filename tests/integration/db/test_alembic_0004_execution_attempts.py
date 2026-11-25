"""Revision 0004 preserves legacy execution summaries and enforces attempt invariants."""

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
PREV = "0003_assignment_user_fks"
HEAD = "0004_execution_attempts"


def _alembic(*args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def _conn() -> psycopg.Connection:
    return psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)


def _attempt(
    conn: psycopg.Connection,
    aid: str,
    execution_id: str,
    ordinal: int = 1,
    result: str = "running",
    trigger_kind: str = "run",
    ended_at: str | None = None,
    approval_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO execution_attempts "
        "(id, execution_id, ordinal, started_at, ended_at, result, lease_owner, "
        "lease_expires_at, trigger_kind, approval_id) "
        "VALUES (%s, %s, %s, '2026-01-01', %s, %s, 'worker', '2026-01-02', %s, %s)",
        (aid, execution_id, ordinal, ended_at, result, trigger_kind, approval_id),
    )


def test_upgrade_constraints_cascades_and_empty_round_trip() -> None:
    tag = secrets.token_hex(5)
    wid = f"w-{tag}"
    ids = {
        status: f"e-{status}-{tag}"
        for status in ("queued", "running", "waiting_approval", "completed")
    }
    approval_id = f"a-{tag}"
    try:
        _alembic("downgrade", PREV)
        with _conn() as conn:
            conn.execute(
                "INSERT INTO workflows (id, name, nodes, edges, updated_at) "
                "VALUES (%s, 'test', '[]', '[]', now())",
                (wid,),
            )
            for status, eid in ids.items():
                conn.execute(
                    "INSERT INTO workflow_executions "
                    "(id, workflow_id, status, node_results, variables, thread_id) "
                    "VALUES (%s, %s, %s, '{}', '{}', %s)",
                    (eid, wid, status, f"thread-{eid}"),
                )
            before = conn.execute(
                "SELECT id, status, node_results, variables FROM workflow_executions "
                "WHERE workflow_id = %s ORDER BY id",
                (wid,),
            ).fetchall()
        _alembic("upgrade", HEAD)
        with _conn() as conn:
            assert conn.execute("SELECT count(*) FROM execution_attempts").fetchone() == (0,)
            after = conn.execute(
                "SELECT id, status, node_results, variables FROM workflow_executions "
                "WHERE workflow_id = %s ORDER BY id",
                (wid,),
            ).fetchall()
            assert after == before
            predicate = conn.execute(
                "SELECT pg_get_expr(i.indpred, i.indrelid) FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'execution_attempts_one_running_per_execution_key'"
            ).fetchone()
            assert predicate is not None and predicate[0] == "(ended_at IS NULL)"

            eid = ids["running"]
            bad: list[tuple[str, Callable[[], None]]] = [
                ("ordinal", lambda: _attempt(conn, f"bad-ordinal-{tag}", eid, ordinal=0)),
                ("result", lambda: _attempt(conn, f"bad-result-{tag}", eid, result="unknown")),
                (
                    "trigger",
                    lambda: _attempt(conn, f"bad-trigger-{tag}", eid, trigger_kind="unknown"),
                ),
                (
                    "open",
                    lambda: _attempt(conn, f"bad-open-{tag}", eid, ended_at="2026-01-02"),
                ),
                (
                    "time",
                    lambda: _attempt(
                        conn,
                        f"bad-time-{tag}",
                        eid,
                        result="failed",
                        ended_at="2025-12-31",
                    ),
                ),
            ]
            for _label, insert_bad_attempt in bad:
                with pytest.raises(psycopg.errors.CheckViolation):
                    insert_bad_attempt()

            _attempt(conn, f"first-{tag}", eid)
            with pytest.raises(psycopg.errors.UniqueViolation):
                _attempt(conn, f"duplicate-ordinal-{tag}", eid)
            with pytest.raises(psycopg.errors.UniqueViolation):
                _attempt(conn, f"duplicate-open-{tag}", eid, ordinal=2)
            conn.execute(
                "INSERT INTO approvals (id, execution_id, node_id, decision) "
                "VALUES (%s, %s, 'node', 'approved')",
                (approval_id, eid),
            )
            _attempt(conn, f"approved-{tag}", ids["queued"], approval_id=approval_id)
            conn.execute(
                "UPDATE workflow_executions SET pending_attempt_approval_id = %s WHERE id = %s",
                (approval_id, ids["queued"]),
            )
            conn.execute("DELETE FROM approvals WHERE id = %s", (approval_id,))
            assert conn.execute(
                "SELECT approval_id FROM execution_attempts WHERE id = %s", (f"approved-{tag}",)
            ).fetchone() == (None,)
            assert conn.execute(
                "SELECT pending_attempt_approval_id FROM workflow_executions WHERE id = %s",
                (ids["queued"],),
            ).fetchone() == (None,)
            conn.execute("DELETE FROM workflow_executions WHERE id = %s", (eid,))
            assert conn.execute(
                "SELECT count(*) FROM execution_attempts WHERE execution_id = %s", (eid,)
            ).fetchone() == (0,)
            conn.execute(
                "DELETE FROM execution_attempts WHERE execution_id = %s",
                (ids["queued"],),
            )
        _alembic("downgrade", PREV)
        _alembic("upgrade", HEAD)
        with _conn() as conn:
            assert conn.execute("SELECT count(*) FROM execution_attempts").fetchone() == (0,)
    finally:
        _alembic("upgrade", HEAD)
        with _conn() as conn:
            conn.execute("DELETE FROM workflows WHERE id = %s", (wid,))
