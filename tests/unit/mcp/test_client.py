"""Tests for MCPClient."""

import json

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.mcp.client import (
    MCPClient,
    MCPHTTPError,
    MCPRpcError,
    MCPTimeoutError,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)


async def test_initialize_plain_json(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "example", "version": "1.0"},
            },
        },
        headers={"content-type": "application/json"},
    )
    client = MCPClient("https://mcp.example.com/rpc")
    result = await client.initialize()
    assert result["serverInfo"]["name"] == "example"


async def test_tools_list_plain_json(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "ask_question",
                        "description": "Ask",
                        "inputSchema": {"type": "object"},
                    },
                ],
            },
        },
    )
    client = MCPClient("https://mcp.example.com/rpc")
    tools = await client.tools_list()
    assert tools == [
        {"name": "ask_question", "description": "Ask", "inputSchema": {"type": "object"}}
    ]


async def test_tools_call_plain_json(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "42"}]},
        },
    )
    client = MCPClient("https://mcp.example.com/rpc")
    result = await client.tools_call("t", {"q": "?"})
    assert result["content"] == [{"type": "text", "text": "42"}]


async def test_tools_list_sse_response(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """Many MCP servers return text/event-stream. Client must handle it."""
    sse_body = (
        "event: message\n"
        "data: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"tools": [{"name": "t", "inputSchema": {}}]},
            }
        )
        + "\n\n"
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        text=sse_body,
        headers={"content-type": "text/event-stream"},
    )
    client = MCPClient("https://mcp.example.com/rpc")
    tools = await client.tools_list()
    assert tools == [{"name": "t", "inputSchema": {}}]


async def test_http_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        status_code=500,
        text="bad",
    )
    client = MCPClient("https://mcp.example.com/rpc")
    with pytest.raises(MCPHTTPError, match="500"):
        await client.tools_list()


async def test_rpc_error_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "method not found"}},
    )
    client = MCPClient("https://mcp.example.com/rpc")
    with pytest.raises(MCPRpcError, match="method not found"):
        await client.tools_list()


async def test_auth_header_sent(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    client = MCPClient(
        "https://mcp.example.com/rpc",
        auth_header={"Authorization": "Bearer abc"},
    )
    await client.tools_list()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("authorization") == "Bearer abc"
