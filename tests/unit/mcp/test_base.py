"""Tests for McpToolProvider."""

import base64
import os
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.mcp.base import McpToolProvider
from src.security.encryption import encrypt
from src.tools.base import ApiKeyAuth, BuildContext, NoAuth


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "dev",
        "name": "Test",
        "url": "https://mcp.example.com/rpc",
        "description": "test server",
        "authType": "none",
        "encryptedAccessToken": None,
        "headerName": None,
        "isShared": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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
    srv = _server_row(authType="api-key", encryptedAccessToken=encrypt("secret"))
    provider = McpToolProvider(srv)
    assert isinstance(provider.auth, ApiKeyAuth)


async def test_provider_oauth_auth_not_implemented_in_3a() -> None:
    srv = _server_row(authType="oauth")
    provider = McpToolProvider(srv)
    with pytest.raises(NotImplementedError, match="Phase 3b"):
        _ = provider.auth  # property access triggers check


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
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "echoed: hello"}]},
        },
    )
    provider = McpToolProvider(_server_row())
    node = _agent_node()
    tool = await provider.build_tool("echo", BuildContext(node=node, state=initial_state()))
    result = await tool.ainvoke({"q": "hello"})
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
    srv = _server_row(authType="api-key", encryptedAccessToken=encrypted, headerName="X-API-KEY")
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
    srv = _server_row(authType="bearer", encryptedAccessToken=encrypted)
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    await McpToolProvider(srv).tools()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("authorization") == "Bearer bearer-token-xyz"
