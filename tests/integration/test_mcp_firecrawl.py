"""Integration — Agent with Firecrawl MCP (api-key via URL template)."""

import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 120.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_agent_with_firecrawl_mcp(client: AsyncClient) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    if not os.environ.get("FIRECRAWL_API_KEY"):
        pytest.skip("FIRECRAWL_API_KEY not set")

    mcp_resp = await client.post(
        "/mcp-servers",
        json={
            "name": "Firecrawl MCP",
            "url": "https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/mcp",
            "authType": "none",  # Firecrawl uses api-key in URL path
            "category": "web",
        },
    )
    assert mcp_resp.status_code == 201, mcp_resp.text
    server_id = mcp_resp.json()["id"]

    try:
        tc_resp = await client.post(f"/mcp-servers/{server_id}/test-connection")
        if not tc_resp.json()["ok"]:
            pytest.skip(f"Firecrawl MCP unreachable: {tc_resp.json()['message']}")

        wf_resp = await client.post(
            "/workflows",
            json={
                "name": "Firecrawl Agent test",
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
                            "instructions": "Use Firecrawl to scrape example.com and summarize in one sentence.",
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
        assert wf_resp.status_code == 201
        start = await client.post(
            "/executions",
            json={"workflowId": wf_resp.json()["id"], "input": ""},
        )
        final = await _poll_until_terminal(client, start.json()["id"])
        assert final["status"] == "completed"
        output = final.get("output")
        assert isinstance(output, str) and len(output) > 0

    finally:
        await client.delete(f"/mcp-servers/{server_id}")
