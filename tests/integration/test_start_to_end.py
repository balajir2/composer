"""Integration test — POST a workflow, run it, observe completion."""

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient, execution_id: str, timeout: float = 10.0
) -> dict[str, object]:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.1)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_start_to_end_completes_with_input_as_output(client: AsyncClient) -> None:
    # 1. Create workflow
    wf_payload = {
        "name": "Phase-1 smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    create_resp = await client.post("/workflows", json=wf_payload)
    assert create_resp.status_code == 201, create_resp.text
    workflow_id: str = create_resp.json()["id"]

    # 2. Start execution
    start_resp = await client.post(
        "/executions", json={"workflowId": workflow_id, "input": "hello world"}
    )
    assert start_resp.status_code == 202, start_resp.text
    execution_id: str = start_resp.json()["id"]
    # POST /executions creates the row as 'queued' and enqueues a Cloud Task;
    # it only becomes 'running' once /internal/claim-and-run actually claims
    # it (ADR-0033) -- which happens asynchronously relative to this response.
    assert start_resp.json()["status"] == "queued"

    # 3. Poll until terminal
    final = await _poll_until_terminal(client, execution_id)
    assert final["status"] == "completed", f"Expected completed, got: {final}"
    assert final["output"] == "hello world"  # see §7.1 deliberate deviation from OAB


async def test_start_to_end_with_dict_input(client: AsyncClient) -> None:
    wf_payload = {
        "name": "Phase-1 dict input",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    wf_id: str = (await client.post("/workflows", json=wf_payload)).json()["id"]

    payload_input = {"msg": "hi", "count": 3}
    start_resp = await client.post(
        "/executions", json={"workflowId": wf_id, "input": payload_input}
    )
    execution_id: str = start_resp.json()["id"]
    final = await _poll_until_terminal(client, execution_id)
    assert final["status"] == "completed"
    assert final["output"] == payload_input
