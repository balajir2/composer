"""Behavioral tests for resolve_mcp_tools_by_names and the permission / not-found
paths of the MCP resolver (src/mcp/resolver.py)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.mcp import resolver
from src.mcp.resolver import (
    McpPermissionError,
    McpServerNotFound,
    resolve_mcp_tools_by_names,
    resolve_single_mcp_tool,
)
from src.tools.base import ToolDefinition


def _server(user_id: str = "owner", is_shared: bool = False) -> Any:
    return SimpleNamespace(id="srv1", userId=user_id, isShared=is_shared)


def _db(server: Any | None) -> MagicMock:
    db = MagicMock()
    db.mcpserver.find_unique = AsyncMock(return_value=server)
    return db


class _FakeProvider:
    """Replaces McpToolProvider; records construction args and built tools."""

    instances: ClassVar[list[_FakeProvider]] = []
    advertised: ClassVar[list[str]] = ["alpha", "beta"]
    tools_error: Exception | None = None

    def __init__(self, server: Any, *, db: Any = None, user_id: str | None = None) -> None:
        self.server = server
        self.user_id = user_id
        self.built: list[str] = []
        _FakeProvider.instances.append(self)

    async def tools(self) -> list[ToolDefinition]:
        if _FakeProvider.tools_error is not None:
            raise _FakeProvider.tools_error
        return [
            ToolDefinition(name=n, description=n, args_schema=BaseModel)
            for n in _FakeProvider.advertised
        ]

    def build_tool_from_def(self, td: ToolDefinition) -> BaseTool:
        self.built.append(td.name)
        tool = MagicMock(spec=BaseTool)
        tool.name = td.name
        return tool

    async def build_tool(self, tool_name: str, context: Any) -> BaseTool:
        tool = MagicMock(spec=BaseTool)
        tool.name = tool_name
        return tool


@pytest.fixture(autouse=True)
def _fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    _FakeProvider.instances = []
    _FakeProvider.advertised = ["alpha", "beta"]
    _FakeProvider.tools_error = None
    monkeypatch.setattr(resolver, "McpToolProvider", _FakeProvider)


async def test_by_names_returns_only_requested_tools_in_requested_order() -> None:
    tools = await resolve_mcp_tools_by_names("srv1", ["beta", "alpha"], "owner", _db(_server()))
    assert [t.name for t in tools] == ["beta", "alpha"]


async def test_by_names_skips_tools_the_server_no_longer_advertises() -> None:
    tools = await resolve_mcp_tools_by_names("srv1", ["alpha", "retired"], "owner", _db(_server()))
    assert [t.name for t in tools] == ["alpha"]


async def test_by_names_empty_selection_returns_every_server_tool() -> None:
    tools = await resolve_mcp_tools_by_names("srv1", [], "owner", _db(_server()))
    assert [t.name for t in tools] == ["alpha", "beta"]


async def test_by_names_missing_server_raises_not_found() -> None:
    with pytest.raises(McpServerNotFound, match="srv1"):
        await resolve_mcp_tools_by_names("srv1", [], "owner", _db(None))
    assert _FakeProvider.instances == []


async def test_by_names_private_server_of_another_user_is_denied_before_any_upstream_call() -> None:
    with pytest.raises(McpPermissionError):
        await resolve_mcp_tools_by_names("srv1", [], "intruder", _db(_server("owner", False)))
    assert _FakeProvider.instances == []


async def test_by_names_shared_server_is_usable_by_other_users() -> None:
    tools = await resolve_mcp_tools_by_names("srv1", [], "someone", _db(_server("owner", True)))
    assert len(tools) == 2
    assert _FakeProvider.instances[0].user_id == "someone"


async def test_by_names_anonymous_user_cannot_use_private_server() -> None:
    with pytest.raises(McpPermissionError):
        await resolve_mcp_tools_by_names("srv1", [], None, _db(_server("owner", False)))


async def test_by_names_upstream_tools_list_failure_propagates() -> None:
    _FakeProvider.tools_error = ConnectionError("tools/list timed out")
    with pytest.raises(ConnectionError, match="timed out"):
        await resolve_mcp_tools_by_names("srv1", ["alpha"], "owner", _db(_server()))


async def test_single_tool_missing_server_raises_not_found() -> None:
    with pytest.raises(McpServerNotFound):
        await resolve_single_mcp_tool("srv1", "alpha", "owner", _db(None))


async def test_single_tool_private_server_of_another_user_is_denied() -> None:
    with pytest.raises(McpPermissionError):
        await resolve_single_mcp_tool("srv1", "alpha", "intruder", _db(_server("owner", False)))
