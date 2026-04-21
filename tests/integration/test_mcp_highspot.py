"""Integration — Agent with Highspot OAuth MCP.

Exercises the refresh → tools/list → tools/call → workflow completion path
against real Highspot. The full browser-driven authorize flow is covered
by unit tests with a mock IdP; this integration seeds a McpOAuthToken
row with a pre-obtained refresh token.

Required env vars:
    HIGHSPOT_OAUTH_CLIENT_ID
    HIGHSPOT_OAUTH_CLIENT_SECRET
    HIGHSPOT_OAUTH_REFRESH_TOKEN
    HIGHSPOT_MCP_URL (e.g., https://mcp.highspot.com/mcp)
    ANTHROPIC_API_KEY
    ENCRYPTION_KEY
"""

import asyncio
import os
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

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
        "HIGHSPOT_OAUTH_CLIENT_ID",
        "HIGHSPOT_OAUTH_CLIENT_SECRET",
        "HIGHSPOT_OAUTH_REFRESH_TOKEN",
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

    # authType='oauth' alone isn't enough — we also need oauthConfig.
    # The POST endpoint doesn't accept oauthConfig yet (out of 3b scope),
    # so set it via a direct Prisma update.
    await db.mcpserver.update(
        where={"id": server_id},
        data={
            "oauthConfig": {
                "authorizeUrl": f"{env['HIGHSPOT_MCP_URL'].rstrip('/mcp')}/oauth/authorize",
                "tokenUrl": f"{env['HIGHSPOT_MCP_URL'].rstrip('/mcp')}/oauth/token",
                "clientId": env["HIGHSPOT_OAUTH_CLIENT_ID"],
                "clientSecret": env["HIGHSPOT_OAUTH_CLIENT_SECRET"],
                "scopes": [],
            },
        },
    )

    # Seed a McpOAuthToken row with the refresh token. Access token empty →
    # first use triggers refresh-on-use.
    await db.mcpoauthtoken.create(
        data={
            "mcpServerId": server_id,
            "userId": "dev",
            "encryptedAccessToken": encrypt(""),  # forces refresh
            "encryptedRefreshToken": encrypt(env["HIGHSPOT_OAUTH_REFRESH_TOKEN"]),
            "expiresAt": None,  # None also forces refresh-on-first-use
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
                    {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
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
                    {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
                ],
                "edges": [
                    {"id": "e1", "source": "s", "target": "a"},
                    {"id": "e2", "source": "a", "target": "e"},
                ],
            },
        )
        assert wf.status_code == 201, wf.text
        start = await client.post(
            "/executions", json={"workflowId": wf.json()["id"], "input": ""},
        )
        final = await _poll_until_terminal(client, start.json()["id"])
        assert final["status"] == "completed", f"Got: {final}"

        # Token row's access token + expires_at should have updated (refresh fired)
        tok = await db.mcpoauthtoken.find_unique(
            where={
                "mcpServerId_userId": {
                    "mcpServerId": server_id,
                    "userId": "dev",
                }
            }
        )
        assert tok is not None
        assert tok.expiresAt is not None  # refresh populated it

    finally:
        await client.delete(f"/mcp-servers/{server_id}")
