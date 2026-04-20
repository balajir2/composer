"""Regression — OAB's simple-agent template (01-simple-agent.ts).

OAB source: D:/GitHub/open-agent-builder/lib/workflow/templates/examples/01-simple-agent.ts
  The canonical 'Agent with no tools, Text output' workflow that every
  OAB user has run at some point.
Ported: 2026-04-20 for Phase 2 to establish Agent regression parity.
"""

import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_oab_simple_agent_regression(client: AsyncClient) -> None:
    """OAB's simple-agent template: Start → Agent → End, Text output, no tools."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    wf_payload = {
        "name": "OAB Regression — simple agent",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "Start"}},
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": "Agent",
                    "name": "SimpleAgent",
                    "model": "anthropic/claude-3-5-haiku-latest",
                    "instructions": "Respond with exactly: 'Regression check OK'.",
                    "outputFormat": "Text",
                    "includeChatHistory": False,
                },
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "End"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    }
    create = await client.post("/workflows", json=wf_payload)
    assert create.status_code == 201, create.text
    start = await client.post(
        "/executions",
        json={"workflowId": create.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed"
    output = final.get("output")
    assert isinstance(output, str) and len(output) > 0
