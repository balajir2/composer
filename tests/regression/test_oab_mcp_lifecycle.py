"""Regression — OAB mcp-lifecycle.spec.ts.

OAB source: D:/GitHub/open-agent-builder/tests/mcp-lifecycle.spec.ts
  "Custom MCP Server Step-by-Step Lifecycle" — add server → retrieve →
  run workflow. Ported against Composer's HTTP API for Phase 3a regression.
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


async def test_oab_mcp_lifecycle_regression(client: AsyncClient) -> None:
    """Port of OAB's mcp-lifecycle spec. Creates a DeepWiki server, verifies
    tools list is populated after test-connection, runs a workflow, asserts
    completion shape."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    # Step 1: Add MCP server
    resp = await client.post(
        "/mcp-servers",
        json={
            "name": "Regression DeepWiki",
            "url": "https://mcp.deepwiki.com/mcp",
            "authType": "none",
        },
    )
    assert resp.status_code == 201, resp.text
    server_id = resp.json()["id"]

    try:
        # Step 2: Test connection — populates the `tools` cache
        tc = await client.post(f"/mcp-servers/{server_id}/test-connection")
        assert tc.status_code == 200
        if not tc.json()["ok"]:
            pytest.skip(f"DeepWiki unreachable: {tc.json()['message']}")

        # Step 3: Verify tools list is populated
        all_servers = (await client.get("/mcp-servers")).json()
        srv = next(s for s in all_servers if s["id"] == server_id)
        assert srv["connectionStatus"] == "connected"
        assert srv.get("tools"), "Tools cache should be populated after test-connection"

        # Step 4: Run a workflow using this MCP
        wf = await client.post(
            "/workflows",
            json={
                "name": "OAB Regression — MCP lifecycle",
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
                            "instructions": "Use DeepWiki to answer in one sentence: when was the Taj Mahal built?",
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
        assert wf.status_code == 201
        start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
        final = await _poll_until_terminal(client, start.json()["id"])
        assert final["status"] == "completed", f"Got: {final}"
        assert isinstance(final.get("output"), str)

    finally:
        await client.delete(f"/mcp-servers/{server_id}")
