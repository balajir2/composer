"""Integration — Start → UserApproval → End (rejected path)."""

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_status(
    client: AsyncClient,
    execution_id: str,
    target: set[str],
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in target:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not reach {target} within {timeout}s")


async def test_user_approval_rejected_path(client: AsyncClient, app: FastAPI) -> None:
    db: Any = app.state.db

    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 5a user-approval (rejected path)",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "ua",
                    "type": "user-approval",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "UA", "approvalMessage": "Please approve"},
                },
                {
                    "id": "ok",
                    "type": "set-state",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "ok", "stateKey": "result", "stateValue": "approved"},
                },
                {
                    "id": "no",
                    "type": "set-state",
                    "position": {"x": 200, "y": 100},
                    "data": {"label": "no", "stateKey": "result", "stateValue": "rejected"},
                },
                {"id": "e", "type": "end", "position": {"x": 300, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ua"},
                {"id": "e2", "source": "ua", "target": "ok", "branch": "approved"},
                {"id": "e3", "source": "ua", "target": "no", "branch": "rejected"},
                {"id": "e4", "source": "ok", "target": "e"},
                {"id": "e5", "source": "no", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
    execution_id = start.json()["id"]

    paused = await _poll_until_status(
        client, execution_id, {"waiting_approval", "failed", "completed"}
    )
    assert paused["status"] == "waiting_approval"

    resume = await client.post(
        f"/executions/{execution_id}/resume",
        json={"decision": "rejected", "note": "needs revisions"},
    )
    assert resume.status_code == 200

    done = await _poll_until_status(client, execution_id, {"completed", "failed"})
    assert done["status"] == "completed", f"Got: {done}"

    final_vars = done.get("variables") or {}
    assert isinstance(final_vars, dict)
    assert final_vars.get("result") == "rejected"

    approvals = await db.approval.find_many(where={"executionId": execution_id})
    assert len(approvals) == 1
    assert approvals[0].decision == "rejected"
    assert approvals[0].note == "needs revisions"
