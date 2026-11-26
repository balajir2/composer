"""Integration — OAB->Composer migration + reconcile against real Neon (Phase 9b)."""

import contextlib
import json
import secrets
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from src.db.models import User, Workflow, WorkflowExecution
from src.migration.reconcile import reconcile_by_email
from src.migration.runner import run_migration
from tests.integration._rows import Rows

pytestmark = pytest.mark.integration


def _write_export(
    tmp_path: Path,
    users: list[dict[str, Any]],
    workflows: list[dict[str, Any]],
    executions: list[dict[str, Any]],
) -> Path:
    export_dir = tmp_path / "oab_export"
    export_dir.mkdir()
    (export_dir / "users.jsonl").write_text(
        "\n".join(json.dumps(u) for u in users), encoding="utf-8"
    )
    (export_dir / "workflows.jsonl").write_text(
        "\n".join(json.dumps(w) for w in workflows), encoding="utf-8"
    )
    (export_dir / "executions.jsonl").write_text(
        "\n".join(json.dumps(e) for e in executions), encoding="utf-8"
    )
    return export_dir


async def test_migration_and_reconciliation_cycle(
    tmp_path: Path,
    client: AsyncClient,
    app: FastAPI,
    rows: Rows,
) -> None:
    email_a = f"phase9-a-{secrets.token_hex(6)}@example.com"

    oab_users = [
        {"_id": "oab-u-a", "clerkId": "user_2A", "email": email_a.upper(), "name": "Alice"},
    ]
    oab_wfs: list[dict[str, Any]] = [
        {
            "_id": f"oab-wf-{secrets.token_hex(4)}",
            "userId": "user_2A",
            "name": "Alice private",
            "nodes": [],
            "edges": [],
            "tags": [],
            "isPublic": False,
            "isTemplate": False,
        },
        {
            "_id": f"oab-wf-{secrets.token_hex(4)}",
            "userId": "user_2A",
            "name": "Alice public",
            "nodes": [],
            "edges": [],
            "tags": [],
            "isPublic": True,
            "isTemplate": False,
        },
    ]
    oab_execs: list[dict[str, Any]] = [
        {
            "_id": f"oab-exec-{secrets.token_hex(4)}",
            "workflowId": oab_wfs[0]["_id"],
            "userId": "user_2A",
            "status": "completed",
            "nodeResults": {},
            "variables": {},
            "threadId": "oab-thread-x",
            "input": None,
            "output": None,
        }
    ]
    export_dir = _write_export(tmp_path, oab_users, oab_wfs, oab_execs)

    # Migration — no MCP data in this fixture, so no OAB key needed
    code = await run_migration(export_dir=str(export_dir), dry_run=False)
    assert code == 0

    try:
        # Verify rows exist with user_id=None + original_owner_email set
        for wf in oab_wfs:
            row = await rows.get(Workflow, wf["_id"])
            assert row is not None
            assert row.user_id is None
            assert row.original_owner_email == email_a.lower()
            assert row.is_public == wf["isPublic"]
        for exec_ in oab_execs:
            row = await rows.get(WorkflowExecution, exec_["_id"])
            assert row is not None
            assert row.user_id is None
            assert row.original_owner_email == email_a.lower()

        # Phase 4a oracle: the migration writer inserts an execution only if it is absent. A
        # second run must not overwrite it, and reconcile must claim only unowned rows whose
        # original owner is this email.
        exec_id = oab_execs[0]["_id"]
        await rows.set(WorkflowExecution, WorkflowExecution.id == exec_id, error="local edit")
        owned = await rows.add(
            WorkflowExecution(
                workflow_id=oab_wfs[0]["_id"],
                user_id="someone-else",
                status="completed",
                thread_id=f"p4a-owned-{secrets.token_hex(4)}",
                original_owner_email=email_a.lower(),
                node_results={},
                variables={},
            )
        )
        stranger = await rows.add(
            WorkflowExecution(
                workflow_id=oab_wfs[0]["_id"],
                status="completed",
                thread_id=f"p4a-stranger-{secrets.token_hex(4)}",
                original_owner_email=f"other-{secrets.token_hex(4)}@example.com",
                node_results={},
                variables={},
            )
        )
        assert await run_migration(export_dir=str(export_dir), dry_run=False) == 0
        again = await rows.get(WorkflowExecution, exec_id)
        assert again is not None and again.error == "local edit" and again.user_id is None
        assert await rows.count(WorkflowExecution, WorkflowExecution.id == exec_id) == 1

        # Phase 4a oracle: an absent input/output stays SQL NULL (not JSON null) when migrated,
        # unlike an execution started through the API, whose absent input is stored as JSON null.
        async with rows.sessionmaker() as s:
            nulls = (
                (
                    await s.execute(
                        text(
                            "SELECT input IS NULL AS i, output IS NULL AS o "
                            "FROM workflow_executions WHERE id = :id"
                        ),
                        {"id": exec_id},
                    )
                )
                .mappings()
                .all()
            )
        assert [dict(r) for r in nulls] == [{"i": True, "o": True}]

        # Register a Composer user with the same email
        reg = await client.post(
            "/auth/register",
            json={"email": email_a, "password": "correct-horse-battery-staple"},
        )
        assert reg.status_code == 201, reg.text
        user_id = reg.json()["id"]

        # Reconcile
        async with app.state.db_sessionmaker() as session:
            result = await reconcile_by_email(session, email_a)
            await session.commit()  # run_reconcile commits; reconcile_by_email does not
        assert result["workflows"] == 2
        assert result["executions"] == 1
        claimed = await rows.get(WorkflowExecution, exec_id)
        assert claimed is not None and claimed.user_id == user_id
        untouched_owned = await rows.get(WorkflowExecution, owned.id)
        assert untouched_owned is not None and untouched_owned.user_id == "someone-else"
        untouched_stranger = await rows.get(WorkflowExecution, stranger.id)
        assert untouched_stranger is not None and untouched_stranger.user_id is None

        # Verify user_id is now set + original_owner_email preserved
        for wf in oab_wfs:
            row = await rows.get(Workflow, wf["_id"])
            assert row is not None
            assert row.user_id == user_id
            assert row.original_owner_email == email_a.lower()

        # Re-running reconcile is a no-op (user_id no longer NULL)
        async with app.state.db_sessionmaker() as session:
            result2 = await reconcile_by_email(session, email_a)
            await session.commit()
        assert result2 == {"workflows": 0, "executions": 0, "mcpServers": 0}
    finally:
        for wf in oab_wfs:
            with contextlib.suppress(Exception):
                await rows.remove(Workflow, Workflow.id == wf["_id"])
        with contextlib.suppress(Exception):
            await rows.remove(User, User.email == email_a.lower())
