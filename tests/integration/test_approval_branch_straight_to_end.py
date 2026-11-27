"""Integration -- an approval's Rejected handle wired straight to the End node
that the Approved path also reaches.

Production regression: saving this topology failed with "End node must have
exactly one incoming edge ... (found 2)" although the two paths are mutually
exclusive. It must save, and run to completion on BOTH decisions.
"""

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

_WORKFLOW: dict[str, Any] = {
    "name": "approval rejected straight to shared end",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {
            "id": "approval-1",
            "type": "user-approval",
            "position": {"x": 100, "y": 0},
            "data": {"label": "Approve?", "approvalMessage": "Please approve"},
        },
        {
            "id": "work",
            "type": "set-state",
            "position": {"x": 200, "y": 0},
            "data": {"label": "work", "stateKey": "result", "stateValue": "approved"},
        },
        {"id": "end-1", "type": "end", "position": {"x": 300, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [
        {"id": "e1", "source": "s", "target": "approval-1"},
        {"id": "e2", "source": "approval-1", "target": "work", "branch": "approved"},
        {"id": "e3", "source": "work", "target": "end-1"},
        {"id": "e4", "source": "approval-1", "target": "end-1", "branch": "rejected"},
    ],
}


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_shared_end_runs_to_completion(client: AsyncClient, decision: str) -> None:
    wf = await client.post("/workflows", json=_WORKFLOW)
    assert wf.status_code == 201, wf.text

    start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
    assert start.status_code == 202, start.text
    execution_id = start.json()["id"]
    assert (await client.get(f"/executions/{execution_id}")).json()["status"] == (
        "waiting_approval"
    )

    resume = await client.post(f"/executions/{execution_id}/resume", json={"decision": decision})
    assert resume.status_code == 200, resume.text

    done = (await client.get(f"/executions/{execution_id}")).json()
    assert done["status"] == "completed", done
    result = (done.get("variables") or {}).get("result")
    assert result == ("approved" if decision == "approved" else None)
