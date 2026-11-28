"""Revision 0006 drops the old checkpoint tables and Prisma's ledger, refuses while an active run
still depends on the old tables, fails fast on a held lock, and downgrades to empty tables with
the original shape."""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from src.db.url import to_libpq_url
from tests.integration.db.schema_catalog import snapshot

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
GOLDEN_0004 = ROOT / "tests" / "fixtures" / "schema" / "golden_0004_langgraph_saver_tables.json"
PREV = "0005_execution_attempts"
OLD = ("langgraph_checkpoint_writes", "langgraph_checkpoints")
EARLY, MID, LATE = (
    "1f000000-0000-6000-8000-000000000001",
    "1f000000-0000-6000-8000-000000000002",
    "1f000000-0000-6000-8000-000000000003",
)


def _url() -> str:
    return to_libpq_url(os.environ["TEST_DATABASE_URL"])


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]},
        capture_output=True,
        text=True,
        timeout=300,
    )


def _alembic(*args: str) -> None:
    done = _run_alembic(*args)
    assert done.returncode == 0, done.stdout + done.stderr


def _conn() -> psycopg.Connection:
    return psycopg.connect(_url(), autocommit=True)


def _present(*names: str) -> set[str]:
    with _conn() as conn:
        return {
            n for n in names if conn.execute("SELECT to_regclass(%s)", (n,)).fetchone() != (None,)
        }


def _version() -> str:
    with _conn() as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row is not None
        return str(row[0])


def _seed(tag: str, status: str, *, old_id: str, new_id: str | None) -> tuple[str, str, str]:
    wid, eid, thread = f"w-{tag}", f"e-{tag}", f"t-{tag}"
    with _conn() as conn:
        conn.execute(
            "INSERT INTO workflows (id, name, nodes, edges, updated_at) "
            "VALUES (%s, 'test', '[]', '[]', now())",
            (wid,),
        )
        conn.execute(
            "INSERT INTO workflow_executions "
            "(id, workflow_id, status, node_results, variables, thread_id) "
            "VALUES (%s, %s, %s, '{}', '{}', %s)",
            (eid, wid, status, thread),
        )
        conn.execute(
            "INSERT INTO langgraph_checkpoints (thread_id, checkpoint_id, checkpoint, metadata) "
            "VALUES (%s, %s, %s, %s)",
            (thread, old_id, b"c", b"m"),
        )
        conn.execute(
            "INSERT INTO langgraph_checkpoint_writes "
            "(thread_id, checkpoint_id, task_id, idx, channel, value) "
            "VALUES (%s, %s, 't', 0, 'ch', %s)",
            (thread, old_id, b"v"),
        )
        if new_id is not None:
            conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) "
                "VALUES (%s, %s, '{}')",
                (thread, new_id),
            )
    return wid, eid, thread


def _cleanup(wid: str, eid: str, thread: str) -> None:
    if not wid:
        return
    with _conn() as conn:
        if _present(*OLD) == set(OLD):
            conn.execute("DELETE FROM langgraph_checkpoint_writes WHERE thread_id = %s", (thread,))
            conn.execute("DELETE FROM langgraph_checkpoints WHERE thread_id = %s", (thread,))
        conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread,))
        conn.execute("DELETE FROM workflow_executions WHERE id = %s", (eid,))
        conn.execute("DELETE FROM workflows WHERE id = %s", (wid,))


def test_head_has_none_of_the_dropped_tables() -> None:
    assert _present(*OLD, "_prisma_migrations") == set()


def test_downgrade_recreates_empty_tables_with_the_original_shape_and_upgrade_drops_them() -> None:
    try:
        _alembic("downgrade", PREV)
        assert _present(*OLD) == set(OLD)
        assert _present("_prisma_migrations") == set()  # D3: not recreated
        with _conn() as conn:
            for table in OLD:
                assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)  # pyright: ignore[reportCallIssue, reportArgumentType]
        catalog = snapshot(_url())
        golden = json.loads(GOLDEN_0004.read_text(encoding="utf-8"))
        for table in OLD:
            assert catalog["columns"][table] == golden["columns"][table]
        for key in ("constraints", "indexes"):
            assert [r for r in catalog[key] if r[0] in OLD] == [
                r for r in golden[key] if r[0] in OLD
            ]
        _alembic("upgrade", "head")
        assert _present(*OLD) == set()
    finally:
        _alembic("upgrade", "head")


@pytest.mark.parametrize(
    ("status", "old_id", "new_id", "refused"),
    [
        ("waiting_approval", MID, None, True),  # only in the old tables
        ("running", MID, None, True),
        ("queued", MID, None, True),
        ("waiting_approval", LATE, MID, True),  # old tables newer: newest state would be lost
        ("waiting_approval", MID, MID, False),  # already copied
        ("waiting_approval", EARLY, MID, False),  # official tables ahead
        ("completed", MID, None, False),  # terminal runs never resume
        ("failed", MID, None, False),
        ("canceled", MID, None, False),
    ],
)
def test_upgrade_refuses_only_while_an_active_run_depends_on_the_old_tables(
    status: str, old_id: str, new_id: str | None, refused: bool
) -> None:
    tag = secrets.token_hex(5)
    wid = eid = thread = ""
    try:
        _alembic("downgrade", PREV)
        wid, eid, thread = _seed(tag, status, old_id=old_id, new_id=new_id)
        done = _run_alembic("upgrade", "head")
        out = done.stdout + done.stderr
        assert "0006: active runs" in out
        if refused:
            assert done.returncode != 0
            assert f"{eid} ({status})" in out
            assert "composer checkpoints copy --to postgres" in out and "6ce5495" in out
            assert thread not in out  # ids and statuses only
            assert _version() == PREV  # nothing applied
            assert _present(*OLD) == set(OLD)  # nothing dropped
        else:
            assert done.returncode == 0, out
            assert _present(*OLD) == set()
    finally:
        _cleanup(wid, eid, thread)
        _alembic("upgrade", "head")


def test_an_orphan_old_checkpoint_with_no_execution_does_not_block() -> None:
    tag = secrets.token_hex(5)
    try:
        _alembic("downgrade", PREV)
        with _conn() as conn:
            conn.execute(
                "INSERT INTO langgraph_checkpoints (thread_id, checkpoint_id, checkpoint, metadata) "
                "VALUES (%s, %s, %s, %s)",
                (f"orphan-{tag}", MID, b"c", b"m"),
            )
        done = _run_alembic("upgrade", "head")
        assert done.returncode == 0, done.stdout + done.stderr
        assert _present(*OLD) == set()
    finally:
        _alembic("upgrade", "head")


def test_upgrade_fails_fast_when_an_old_table_is_locked() -> None:
    _alembic("downgrade", PREV)
    blocker = psycopg.connect(_url())  # not autocommit: the lock is held until close()
    try:
        blocker.execute("LOCK TABLE langgraph_checkpoints IN ROW EXCLUSIVE MODE")
        blocked = _run_alembic("upgrade", "head")
        assert blocked.returncode != 0
        assert "lock timeout" in (blocked.stdout + blocked.stderr).lower()
        assert _version() == PREV and _present(*OLD) == set(OLD)
    finally:
        blocker.close()
        _alembic("upgrade", "head")
