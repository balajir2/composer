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


async def test_auth_header_factory_is_called_per_request(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """For OAuth: the factory must fire on every _rpc call so near-expiry refreshes apply."""
    call_count = {"n": 0}

    async def _factory() -> dict[str, str]:
        call_count["n"] += 1
        return {"Authorization": f"Bearer token-{call_count['n']}"}

    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
    )

    client = MCPClient(
        "https://mcp.example.com/rpc",
        auth_header_factory=_factory,
    )
    await client.tools_list()
    await client.tools_list()

    # Two outbound requests → two factory calls
    assert call_count["n"] == 2
    reqs = httpx_mock.get_requests()
    assert reqs[0].headers.get("authorization") == "Bearer token-1"
    assert reqs[1].headers.get("authorization") == "Bearer token-2"


async def test_auth_header_factory_takes_precedence_over_dict(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    """If both auth_header and auth_header_factory are provided, factory wins."""

    async def _factory() -> dict[str, str]:
        return {"Authorization": "Bearer factory-wins"}

    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    client = MCPClient(
        "https://mcp.example.com/rpc",
        auth_header={"Authorization": "Bearer dict-loses"},
        auth_header_factory=_factory,
    )
    await client.tools_list()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("authorization") == "Bearer factory-wins"


async def test_session_id_from_initialize_is_sent_on_followup_request(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Stateful streamable-HTTP servers require Mcp-Session-Id after initialize."""
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stateful", "version": "1.0"},
            },
        },
        headers={"mcp-session-id": "sess-123"},
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
    )

    client = MCPClient("https://mcp.example.com/rpc")
    await client.initialize()
    await client.tools_list()

    reqs = httpx_mock.get_requests()
    assert reqs[0].headers.get("mcp-session-id") is None
    assert reqs[1].headers.get("mcp-session-id") == "sess-123"


async def test_tools_list_initializes_and_retries_when_session_required(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Atlassian Rovo MCP returns this 400 when tools/list is sent without a session."""
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        status_code=400,
        json={
            "jsonrpc": "2.0",
            "error": {
                "code": -32600,
                "message": "Request must be an initialize request if no session ID is provided.",
            },
        },
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stateful", "version": "1.0"},
            },
        },
        headers={"Mcp-Session-Id": "sess-456"},
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "result": {"tools": [{"name": "create_issue", "inputSchema": {}}]},
        },
    )

    client = MCPClient("https://mcp.example.com/rpc")
    tools = await client.tools_list()

    assert tools == [{"name": "create_issue", "inputSchema": {}}]
    reqs = httpx_mock.get_requests()
    assert len(reqs) == 3
    assert reqs[0].headers.get("mcp-session-id") is None
    assert reqs[1].headers.get("mcp-session-id") is None
    assert reqs[2].headers.get("mcp-session-id") == "sess-456"
