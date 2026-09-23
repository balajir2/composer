"""Integration — OAB->Composer migration + reconcile against real Neon (Phase 9b)."""

import contextlib
import json
import secrets
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from src.migration.reconcile import reconcile_by_email
from src.migration.runner import run_migration

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
) -> None:
    db: Any = app.state.db
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
        }
    ]
    export_dir = _write_export(tmp_path, oab_users, oab_wfs, oab_execs)

    # Migration — no MCP data in this fixture, so no OAB key needed
    code = await run_migration(export_dir=str(export_dir), dry_run=False)
    assert code == 0

    try:
        # Verify rows exist with user_id=None + original_owner_email set
        for wf in oab_wfs:
            row = await db.workflow.find_unique(where={"id": wf["_id"]})
            assert row is not None
            assert row.userId is None
            assert row.originalOwnerEmail == email_a.lower()
            assert row.isPublic == wf["isPublic"]
        for exec_ in oab_execs:
            row = await db.workflowexecution.find_unique(where={"id": exec_["_id"]})
            assert row is not None
            assert row.userId is None
            assert row.originalOwnerEmail == email_a.lower()

        # Register a Composer user with the same email
        reg = await client.post(
            "/auth/register",
            json={"email": email_a, "password": "correct-horse-battery-staple"},
        )
        assert reg.status_code == 201, reg.text
        user_id = reg.json()["id"]

        # Reconcile
        result = await reconcile_by_email(db, email_a)
        assert result["workflows"] == 2
        assert result["executions"] == 1

        # Verify user_id is now set + original_owner_email preserved
        for wf in oab_wfs:
            row = await db.workflow.find_unique(where={"id": wf["_id"]})
            assert row is not None
            assert row.userId == user_id
            assert row.originalOwnerEmail == email_a.lower()

        # Re-running reconcile is a no-op (user_id no longer NULL)
        result2 = await reconcile_by_email(db, email_a)
        assert result2 == {"workflows": 0, "executions": 0, "mcpServers": 0}
    finally:
        for wf in oab_wfs:
            with contextlib.suppress(Exception):
                await db.workflow.delete(where={"id": wf["_id"]})
        with contextlib.suppress(Exception):
            await db.user.delete(where={"email": email_a.lower()})
