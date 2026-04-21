"""Integration — Agent with Highspot OAuth MCP.

Exercises server-side token retrieval (fix #4) + the real MCP protocol
against Highspot. The full OAuth authorize + refresh flow is covered by
unit tests (asserting `resource` on all four flows) and the OAB regression
port; the real refresh endpoint is managed outside the MCP and not
reachable for this test, so we seed a known-valid short-lived access
token directly rather than exercising the refresh path.

Required env vars:
    HIGHSPOT_OAUTH_ACCESS_TOKEN — a currently-valid access token
    HIGHSPOT_MCP_URL (e.g., https://mcp.highspot.com/mcp)
    ANTHROPIC_API_KEY
    ENCRYPTION_KEY
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from prisma import Json  # pyright: ignore[reportAttributeAccessIssue]
from src.security.encryption import encrypt

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 180.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


def _required_env() -> dict[str, str] | None:
    needed = [
        "HIGHSPOT_OAUTH_ACCESS_TOKEN",
        "HIGHSPOT_MCP_URL",
        "ANTHROPIC_API_KEY",
        "ENCRYPTION_KEY",
    ]
    values: dict[str, str] = {}
    for name in needed:
        v = os.environ.get(name)
        if not v:
            return None
        values[name] = v
    return values


async def test_agent_with_highspot_oauth(client: AsyncClient, app: FastAPI) -> None:
    env = _required_env()
    if env is None:
        pytest.skip("Highspot OAuth env vars not set")

    db: Any = app.state.db

    # Register the Highspot MCP server
    mcp_resp = await client.post(
        "/mcp-servers",
        json={
            "name": "Highspot OAuth Test",
            "url": env["HIGHSPOT_MCP_URL"],
            "authType": "oauth",
            "isShared": False,
        },
    )
    assert mcp_resp.status_code == 201, mcp_resp.text
    server_id = mcp_resp.json()["id"]

    # Set a minimal oauthConfig — clientId/clientSecret/tokenUrl are
    # required by the provider construction but aren't actually hit
    # because we seed a long-lived access token below. Real OAuth refresh
    # endpoints are covered by the unit + regression tests.
    await db.mcpserver.update(
        where={"id": server_id},
        data={
            "oauthConfig": Json(
                {
                    "authorizeUrl": "https://example.invalid/oauth/authorize",
                    "tokenUrl": "https://example.invalid/oauth/token",
                    "clientId": "test-client",
                    "clientSecret": "test-secret",
                    "scopes": [],
                }
            ),
        },
    )

    # Seed a valid access token with far-future expiry so refresh is never
    # triggered. The goal is to verify server-side token retrieval (fix #4)
    # + real MCP protocol against Highspot, not the OAuth refresh flow.
    await db.mcpoauthtoken.create(
        data={
            "mcpServerId": server_id,
            "userId": "dev",
            "encryptedAccessToken": encrypt(env["HIGHSPOT_OAUTH_ACCESS_TOKEN"]),
            "encryptedRefreshToken": None,
            "expiresAt": datetime.now(UTC) + timedelta(hours=1),
            "tokenType": "Bearer",
        }
    )

    try:
        # Run Agent workflow using Highspot MCP
        wf = await client.post(
            "/workflows",
            json={
                "name": "Highspot OAuth Test",
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
                            "instructions": "Use Highspot tools to list a few recent spots or content items. One sentence summary.",
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
        assert wf.status_code == 201, wf.text
        start = await client.post(
            "/executions",
            json={"workflowId": wf.json()["id"], "input": ""},
        )
        final = await _poll_until_terminal(client, start.json()["id"])
        assert final["status"] == "completed", f"Got: {final}"

    finally:
        await client.delete(f"/mcp-servers/{server_id}")
