"""Integration — Agent with DeepWiki MCP server.

DeepWiki is a public, no-auth MCP server. Proves the MCP pipeline end-to-end
without requiring any keys (other than an LLM).
"""

import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 90.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_agent_with_deepwiki_mcp(client: AsyncClient) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    # Register DeepWiki MCP server
    mcp_resp = await client.post(
        "/mcp-servers",
        json={
            "name": "DeepWiki",
            "url": "https://mcp.deepwiki.com/mcp",
            "authType": "none",
            "category": "data",
        },
    )
    assert mcp_resp.status_code == 201, mcp_resp.text
    server_id = mcp_resp.json()["id"]

    try:
        # Test connection
        tc_resp = await client.post(f"/mcp-servers/{server_id}/test-connection")
        assert tc_resp.status_code == 200
        if not tc_resp.json()["ok"]:
            pytest.skip(f"DeepWiki unreachable: {tc_resp.json()['message']}")

        # Create workflow using DeepWiki MCP
        wf_resp = await client.post(
            "/workflows",
            json={
                "name": "DeepWiki Agent test",
                "nodes": [
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "a",
                        "type": "agent",
                        "position": {"x": 100, "y": 0},
                        "data": {
                            "label": "Agent",
                            "model": "anthropic/claude-haiku-4-5-20251001",
                            "instructions": "Use DeepWiki tools to find when the Eiffel Tower was completed. One sentence answer.",
                            "outputFormat": "Text",
                            "mcpServerIds": [server_id],
                        },
                    },
                    {
                        "id": "e",
                        "type": "end",
                        "position": {"x": 200, "y": 0},
                        "data": {"label": "E"},
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "s", "target": "a"},
                    {"id": "e2", "source": "a", "target": "e"},
                ],
            },
        )
        assert wf_resp.status_code == 201, wf_resp.text
        wf_id = wf_resp.json()["id"]

        start = await client.post(
            "/executions",
            json={"workflowId": wf_id, "input": ""},
        )
        assert start.status_code == 202, start.text
        final = await _poll_until_terminal(client, start.json()["id"])
        assert final["status"] == "completed", f"Got: {final}"
        output = final.get("output")
        assert isinstance(output, str) and len(output) > 0

    finally:
        # Clean up
        await client.delete(f"/mcp-servers/{server_id}")
