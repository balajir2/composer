"""Tests for the tool registry + resolve_tools_for_node."""

from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import (
    BuildContext,
    NoAuth,
    ToolDefinition,
    ToolProvider,
)
from src.tools.registry import (
    UnknownProviderError,
    UnknownToolError,
    get_provider,
    list_providers,
    register_tool_provider,
    resolve_tools_for_node,
)


class _TestInput(BaseModel):
    q: str


class _TestTool(BaseTool):
    name: str = "registry_test_search"
    description: str = "registry test"

    def __init__(self, **kwargs: Any) -> None:  # pyright: ignore[reportUnusedParameter]
        super().__init__(**kwargs)

    @property
    def args_schema(self) -> type[BaseModel]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return _TestInput

    def _run(self, q: str) -> str:
        return f"searched: {q}"

    async def _arun(self, q: str) -> str:
        return f"searched: {q}"


@register_tool_provider
class _RegistryTestProvider(ToolProvider):  # pyright: ignore[reportUnusedClass]
    name = "_registry_test"
    description = "test-only provider"
    category = "standard"
    auth = NoAuth()

    async def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="registry_test_search",
                description="t",
                args_schema=_TestInput,
            )
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name == "registry_test_search":
            return _TestTool()
        raise UnknownToolError(f"no {tool_name}")


def test_get_provider_returns_registered() -> None:
    p = get_provider("_registry_test")
    assert p.name == "_registry_test"


def test_get_provider_unknown_raises() -> None:
    with pytest.raises(UnknownProviderError, match="_not_registered"):
        get_provider("_not_registered")


def test_list_providers_returns_all_sorted() -> None:
    names = [p.name for p in list_providers()]
    assert "_registry_test" in names
    assert names == sorted(names)


def test_list_providers_filters_by_category() -> None:
    standards = list_providers(category="standard")
    assert all(p.category == "standard" for p in standards)
    assert "_registry_test" in {p.name for p in standards}


def _agent_node(selected_tools: list[str]) -> AgentNode:
    return AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "Agent", "selectedTools": selected_tools},
        }
    )


async def test_resolve_tools_for_node_empty() -> None:
    node = _agent_node([])
    tools = await resolve_tools_for_node(
        node,
        BuildContext(node=node, state=initial_state(), user_id=None),
    )
    assert tools == []


async def test_resolve_tools_for_node_resolves_qualified_name() -> None:
    node = _agent_node(["_registry_test.registry_test_search"])
    tools = await resolve_tools_for_node(
        node,
        BuildContext(node=node, state=initial_state(), user_id=None),
    )
    assert len(tools) == 1
    assert isinstance(tools[0], _TestTool)


async def test_resolve_tools_for_node_unknown_provider_raises() -> None:
    node = _agent_node(["_ghost.search"])
    with pytest.raises(UnknownProviderError, match="_ghost"):
        await resolve_tools_for_node(
            node,
            BuildContext(node=node, state=initial_state(), user_id=None),
        )


async def test_resolve_tools_for_node_mcp_ids_delegate_to_mcp_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3a: mcp_server_ids on a node dispatches to src.mcp.resolver."""
    from unittest.mock import MagicMock

    fake_tool = MagicMock(name="fake_mcp_tool")

    import src.mcp.resolver as resolver_mod

    async def _fake_resolve(node: Any, context: Any, db: Any) -> list[Any]:
        return [fake_tool]

    monkeypatch.setattr(resolver_mod, "resolve_mcp_tools_for_node", _fake_resolve)

    node = AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "A", "mcpServerIds": ["s1"]},
        }
    )
    out = await resolve_tools_for_node(
        node,
        BuildContext(node=node, state=initial_state(), user_id="dev", db=MagicMock()),
    )
    assert out == [fake_tool]


async def test_resolve_tools_for_node_mcp_without_db_raises() -> None:
    """Calling resolve_tools_for_node with mcp_server_ids but no db in context
    is a programmer error — the Agent executor forgot to populate context.db."""
    node = AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "A", "mcpServerIds": ["s1"]},
        }
    )
    with pytest.raises(RuntimeError, match=r"BuildContext\.db is required"):
        await resolve_tools_for_node(
            node,
            BuildContext(node=node, state=initial_state(), user_id="dev", db=None),
        )


def test_duplicate_registration_raises() -> None:
    with pytest.raises(ValueError, match="Duplicate provider"):

        @register_tool_provider
        class _DupProvider(ToolProvider):
            name = "_registry_test"
            description = "dup"
            category = "standard"
            auth = NoAuth()

            async def tools(self) -> list[ToolDefinition]:
                return []

            async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
                raise NotImplementedError

        _ = _DupProvider
