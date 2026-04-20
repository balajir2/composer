"""Integration — Start→Agent(with Tavily)→End against real Tavily + LLM APIs.

Verifies the full tool-binding path: Agent executor resolves
tavily.tavily_search via the ToolProvider framework, the LLM decides to
call it, the tool runs an HTTP call to Tavily, result flows back through
the agentic loop.
"""

import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 60.0,
) -> dict[str, object]:
    """Poll execution status until terminal state or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_agent_uses_tavily_tool(client: AsyncClient) -> None:
    """Agent with tavily.tavily_search tool against real Tavily API.

    Skips if ANTHROPIC_API_KEY or TAVILY_API_KEY is unset.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    if not os.environ.get("TAVILY_API_KEY"):
        pytest.skip("TAVILY_API_KEY not set")

    wf_payload = {
        "name": "Phase-2 Agent+Tavily smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": "Agent",
                    "model": "anthropic/claude-3-5-haiku-latest",
                    "instructions": (
                        "Use the tavily_search tool to find the current population of Tokyo, "
                        "then report the number as a single sentence."
                    ),
                    "outputFormat": "Text",
                    "selectedTools": ["tavily.tavily_search"],
                },
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    }
    create = await client.post("/workflows", json=wf_payload)
    assert create.status_code == 201, create.text
    workflow_id = create.json()["id"]

    start = await client.post(
        "/executions",
        json={"workflowId": workflow_id, "input": ""},
    )
    assert start.status_code == 202, start.text

    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    output = final.get("output")
    assert isinstance(output, str) and len(output) > 0
