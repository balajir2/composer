"""Tests for McpToolProvider."""

import base64
import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.db.repos.mcp_servers import McpServerRecord
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.mcp.base import McpToolProvider
from src.security.encryption import encrypt
from src.tools.base import ApiKeyAuth, BuildContext, NoAuth
from tests.unit.mcp._server_fakes import make_server


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server_row(**overrides: Any) -> McpServerRecord:
    base: dict[str, Any] = {
        "id": "srv1",
        "user_id": "dev",
        "name": "Test",
        "url": "https://mcp.example.com/rpc",
        "description": "test server",
        "auth_type": "none",
        "encrypted_access_token": None,
        "header_name": None,
        "is_shared": False,
    }
    base.update(overrides)
    return make_server(**base)


def _agent_node() -> AgentNode:
    return AgentNode.model_validate(
        {"id": "a", "type": "agent", "position": {"x": 0, "y": 0}, "data": {"label": "A"}}
    )


async def test_provider_metadata_no_auth() -> None:
    provider = McpToolProvider(_server_row())
    assert provider.name == "mcp:srv1"
    assert provider.category == "mcp"
    assert isinstance(provider.auth, NoAuth)


async def test_provider_metadata_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    srv = _server_row(auth_type="api-key", encrypted_access_token=encrypt("secret"))
    provider = McpToolProvider(srv)
    assert isinstance(provider.auth, ApiKeyAuth)


async def test_tools_calls_tools_list(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "echoes",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"q": {"type": "string"}},
                            "required": ["q"],
                        },
                    }
                ]
            },
        },
    )
    provider = McpToolProvider(_server_row())
    tools = await provider.tools()
    assert [t.name for t in tools] == ["echo"]


async def test_build_tool_returns_invocable(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    # build_tool now fetches tools/list so it can bind the real input
    # schema to the LangChain tool (fixes firecrawl rejecting calls that
    # omit required params like `prompt`).  Mock tools/list first.
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echoes its input",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"q": {"type": "string"}},
                        },
                    }
                ]
            },
        },
    )
    # Then the actual tools/call that the tool invocation triggers.
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "echoed: hello"}]},
        },
    )
    provider = McpToolProvider(_server_row())
    node = _agent_node()
    tool = await provider.build_tool("echo", BuildContext(node=node, state=initial_state()))
    result = await tool.ainvoke({"q": "hello"})  # pyright: ignore[reportUnknownMemberType]
    assert "echoed: hello" in result


async def test_health_check_ok(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    # initialize
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
    )
    # tools/list
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "t", "inputSchema": {}}]}},
    )
    provider = McpToolProvider(_server_row())
    status = await provider.health_check()
    assert status.ok is True
    assert "1 tool" in status.message


async def test_health_check_fail(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        status_code=500,
        text="error",
    )
    provider = McpToolProvider(_server_row())
    status = await provider.health_check()
    assert status.ok is False
    assert "500" in status.message or "MCPHTTPError" in status.message


async def test_api_key_auth_header_decrypted_on_demand(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_encryption_key(monkeypatch)
    encrypted = encrypt("the-secret-key")
    srv = _server_row(
        auth_type="api-key", encrypted_access_token=encrypted, header_name="X-API-KEY"
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    provider = McpToolProvider(srv)
    await provider.tools()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("x-api-key") == "the-secret-key"


async def test_bearer_auth_adds_bearer_prefix(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_encryption_key(monkeypatch)
    encrypted = encrypt("bearer-token-xyz")
    srv = _server_row(auth_type="bearer", encrypted_access_token=encrypted)
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    await McpToolProvider(srv).tools()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("authorization") == "Bearer bearer-token-xyz"


async def test_oauth_auth_returns_oauthauth_descriptor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 3b: auth_type='oauth' no longer raises NotImplementedError."""
    from unittest.mock import MagicMock

    _set_encryption_key(monkeypatch)
    srv = _server_row(
        auth_type="oauth",
        oauth_config={
            "authorizeUrl": "https://idp/auth",
            "tokenUrl": "https://idp/token",
            "clientId": "c",
            "clientSecret": "s",
            "scopes": ["read"],
        },
    )
    from src.tools.base import OAuthAuth

    provider = McpToolProvider(srv, sessionmaker=MagicMock(), user_id="user1")
    assert isinstance(provider.auth, OAuthAuth)
    assert provider.auth.include_rfc8707_resource is True


async def test_oauth_auth_header_factory_calls_get_valid_access_token(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """When an OAuth provider's client fires a request, the auth header factory
    must resolve via oauth.get_valid_access_token (fix #4)."""
    from unittest.mock import MagicMock

    _set_encryption_key(monkeypatch)
    srv = _server_row(
        auth_type="oauth",
        oauth_config={
            "authorizeUrl": "https://idp/auth",
            "tokenUrl": "https://idp/token",
            "clientId": "c",
            "clientSecret": "s",
            "scopes": [],
        },
    )

    captured: dict[str, Any] = {}

    async def _fake_get_valid(server: Any, user_id: str, sessionmaker: Any) -> str:
        captured["server_id"] = server.id
        captured["user_id"] = user_id
        return "bearer-xyz"

    import src.mcp.base as base_mod

    monkeypatch.setattr(base_mod, "get_valid_access_token", _fake_get_valid)

    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )

    provider = McpToolProvider(srv, sessionmaker=MagicMock(), user_id="user1")
    await provider.tools()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("authorization") == "Bearer bearer-xyz"
    assert captured == {"server_id": "srv1", "user_id": "user1"}


@pytest.mark.parametrize(
    ("sessionmaker", "user_id"), [(None, None), (None, "user1"), (MagicMock(), None)]
)
def test_oauth_provider_without_sessionmaker_or_user_id_fails_fast(
    sessionmaker: Any, user_id: str | None
) -> None:
    """OAuth McpToolProvider requires sessionmaker+user_id at construction, so a caller that wires it
    up incorrectly fails immediately instead of on the first outbound request."""
    srv = _server_row(auth_type="oauth", oauth_config={"tokenUrl": "x", "clientId": "c"})
    with pytest.raises(ValueError, match="requires sessionmaker and user_id"):
        McpToolProvider(srv, sessionmaker=sessionmaker, user_id=user_id)


def test_static_auth_provider_does_not_need_db_or_user_id() -> None:
    McpToolProvider(_server_row(auth_type="none"))
