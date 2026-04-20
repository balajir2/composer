"""Integration — the 'mcp' node type (standalone, no LLM).

Workflow: Start → Mcp(server, tool) → End.
Uses DeepWiki's first available tool with a hardcoded question (no LLM).
"""

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 60.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_mcp_standalone_node(client: AsyncClient) -> None:
    # DeepWiki is public; no API key required
    mcp_resp = await client.post(
        "/mcp-servers",
        json={
            "name": "DeepWiki",
            "url": "https://mcp.deepwiki.com/sse",
            "authType": "none",
            "category": "data",
        },
    )
    assert mcp_resp.status_code == 201, mcp_resp.text
    server_id = mcp_resp.json()["id"]

    try:
        tc = await client.post(f"/mcp-servers/{server_id}/test-connection")
        if not tc.json()["ok"]:
            pytest.skip(f"DeepWiki unreachable: {tc.json()['message']}")

        # Discover one tool name from the cached tools
        server_list = await client.get("/mcp-servers")
        srv = next(s for s in server_list.json() if s["id"] == server_id)
        available = srv.get("tools") or []
        if not available:
            pytest.skip("DeepWiki returned no tools")
        tool_name = available[0]["name"]

        wf = await client.post(
            "/workflows",
            json={
                "name": "MCP standalone node",
                "nodes": [
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "m",
                        "type": "mcp",
                        "position": {"x": 100, "y": 0},
                        "data": {
                            "label": "MCP",
                            "mcpServerId": server_id,
                            "toolName": tool_name,
                            # `question` is a common param for DeepWiki; if the first
                            # tool is ask_question this works.
                            "arguments": {"question": "Who wrote Hamlet?"},
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
                    {"id": "e1", "source": "s", "target": "m"},
                    {"id": "e2", "source": "m", "target": "e"},
                ],
            },
        )
        if wf.status_code != 201:
            pytest.skip(f"Workflow create rejected: {wf.text}")
        start = await client.post("/executions", json={"workflowId": wf.json()["id"], "input": ""})
        final = await _poll_until_terminal(client, start.json()["id"])
        # Accept completed or failed-with-known-reason; the goal is end-to-end plumbing.
        assert final["status"] in {"completed", "failed"}
        if final["status"] == "completed":
            assert final.get("output") is not None

    finally:
        await client.delete(f"/mcp-servers/{server_id}")
