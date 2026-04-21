"""Integration — start → set-state(list) → join-chunks → end (real Neon)."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_status(
    client: AsyncClient,
    execution_id: str,
    target: set[str],
    timeout: float = 20.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, Any] = resp.json()
        if body["status"] in target:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not reach {target} within {timeout}s")


async def test_join_chunks_concatenates_list_from_upstream(client: AsyncClient) -> None:
    """set-state writes a list to state.chunks; join-chunks concatenates it
    into lastOutput with a custom separator."""
    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 6a join-chunks integration",
            "nodes": [
                {
                    "id": "s",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "S"},
                },
                {
                    "id": "ss",
                    "type": "set-state",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "label": "SS",
                        "stateKey": "chunks",
                        "stateValue": ["alpha", "beta", "gamma"],
                    },
                },
                {
                    "id": "jc",
                    "type": "join-chunks",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "JC",
                        "joinChunksVariable": "chunks",
                        "joinChunksSeparator": " | ",
                    },
                },
                {
                    "id": "e",
                    "type": "end",
                    "position": {"x": 300, "y": 0},
                    "data": {"label": "E"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "ss"},
                {"id": "e2", "source": "ss", "target": "jc"},
                {"id": "e3", "source": "jc", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
    execution_id = start.json()["id"]

    done = await _poll_until_status(client, execution_id, {"completed", "failed"})
    assert done["status"] == "completed", f"Got: {done}"

    variables = done.get("variables") or {}
    assert isinstance(variables, dict)
    assert variables.get("lastOutput") == "alpha | beta | gamma"
