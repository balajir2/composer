"""Tests for src/mcp/resolver.py."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.mcp.resolver import (
    McpPermissionError,
    McpServerNotFound,
    resolve_mcp_tools_for_node,
    resolve_single_mcp_tool,
)
from src.tools.base import BuildContext


def _server(id_: str = "srv1", user_id: str = "dev", is_shared: bool = False) -> Any:
    return SimpleNamespace(
        id=id_,
        userId=user_id,
        name="T",
        url="https://m.example.com/rpc",
        description=None,
        authType="none",
        encryptedAccessToken=None,
        headerName=None,
        isShared=is_shared,
    )


def _agent_node(mcp_server_ids: list[str]) -> AgentNode:
    return AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "Agent", "mcpServerIds": mcp_server_ids},
        }
    )


def _mock_db(server: Any | None) -> MagicMock:
    db = MagicMock()
    db.mcpserver = MagicMock()
    db.mcpserver.find_unique = AsyncMock(return_value=server)
    return db


async def test_resolve_empty_ids_returns_empty() -> None:
    node = _agent_node([])
    db = _mock_db(None)
    result = await resolve_mcp_tools_for_node(
        node, BuildContext(node=node, state=initial_state(), user_id="dev"), db
    )
    assert result == []


async def test_resolve_not_found_raises() -> None:
    node = _agent_node(["ghost"])
    db = _mock_db(None)
    with pytest.raises(McpServerNotFound, match="ghost"):
        await resolve_mcp_tools_for_node(
            node,
            BuildContext(node=node, state=initial_state(), user_id="dev"),
            db,
        )


async def test_resolve_owner_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner can use their own non-shared server; this exercises the permission check.

    We patch McpToolProvider on the resolver module so no real HTTP happens.
    """
    from src.mcp import resolver

    class _FakeProvider:
        def __init__(self, server: Any, *, db: Any = None, user_id: str | None = None) -> None:
            self.server = server

        async def tools(self) -> list[Any]:
            from pydantic import BaseModel

            from src.tools.base import ToolDefinition

            class _In(BaseModel):
                x: str = ""

            return [ToolDefinition(name="echo", description="e", args_schema=_In)]

        async def build_tool(self, tool_name: str, context: Any) -> Any:
            return MagicMock()

    monkeypatch.setattr(resolver, "McpToolProvider", _FakeProvider)

    node = _agent_node(["srv1"])
    db = _mock_db(_server(user_id="dev", is_shared=False))
    result = await resolve_mcp_tools_for_node(
        node,
        BuildContext(node=node, state=initial_state(), user_id="dev"),
        db,
    )
    assert len(result) == 1


async def test_resolve_shared_allowed_for_other_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import BaseModel

    from src.mcp import resolver

    class _In(BaseModel):
        x: str = ""

    class _FakeProvider:
        def __init__(self, server: Any, *, db: Any = None, user_id: str | None = None) -> None:
            pass

        async def tools(self) -> list[Any]:
            from src.tools.base import ToolDefinition

            return [ToolDefinition(name="t", description="", args_schema=_In)]

        async def build_tool(self, n: str, c: Any) -> Any:
            return MagicMock()

    monkeypatch.setattr(resolver, "McpToolProvider", _FakeProvider)

    node = _agent_node(["srv1"])
    db = _mock_db(_server(user_id="other-user", is_shared=True))
    result = await resolve_mcp_tools_for_node(
        node, BuildContext(node=node, state=initial_state(), user_id="dev"), db
    )
    assert len(result) == 1


async def test_resolve_private_server_for_other_user_denied() -> None:
    node = _agent_node(["srv1"])
    db = _mock_db(_server(user_id="other-user", is_shared=False))
    with pytest.raises(McpPermissionError, match="dev"):
        await resolve_mcp_tools_for_node(
            node,
            BuildContext(node=node, state=initial_state(), user_id="dev"),
            db,
        )


async def test_resolve_single_mcp_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.mcp import resolver

    fake_tool = MagicMock()

    class _FakeProvider:
        def __init__(self, server: Any, *, db: Any = None, user_id: str | None = None) -> None:
            self.server = server

        async def build_tool(self, name: str, ctx: Any) -> Any:
            return fake_tool

    monkeypatch.setattr(resolver, "McpToolProvider", _FakeProvider)

    db = _mock_db(_server())
    _provider, tool = await resolve_single_mcp_tool("srv1", "echo", "dev", db)
    assert tool is fake_tool
