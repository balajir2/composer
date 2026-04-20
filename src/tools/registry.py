"""Tool registry + node-level tool resolution.

See ADR-0009; spec §8.3.
"""

from typing import Literal, TypeVar

from langchain_core.tools import BaseTool

from src.engine.workflow import AgentNode
from src.tools.base import BuildContext, ToolProvider


class UnknownProviderError(ValueError):
    """Raised when a requested provider isn't registered."""


class UnknownToolError(ValueError):
    """Raised when a provider doesn't offer the requested tool name."""


T = TypeVar("T", bound=ToolProvider)
_PROVIDERS: dict[str, ToolProvider] = {}


def register_tool_provider(cls: type[T]) -> type[T]:
    """Class decorator — instantiates the provider and stores it in the registry."""
    instance = cls()
    if instance.name in _PROVIDERS:
        raise ValueError(
            f"Duplicate provider name: {instance.name!r} "
            f"(already registered as {type(_PROVIDERS[instance.name]).__name__})"
        )
    _PROVIDERS[instance.name] = instance
    return cls


def get_provider(name: str) -> ToolProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise UnknownProviderError(
            f"No provider registered for {name!r}. Registered: {sorted(_PROVIDERS)}"
        ) from exc


def list_providers(*, category: Literal["standard", "mcp"] | None = None) -> list[ToolProvider]:
    """Enumerate registered providers, optionally filtered by category."""
    out = list(_PROVIDERS.values())
    if category is not None:
        out = [p for p in out if p.category == category]
    return sorted(out, key=lambda p: p.name)


def _split_qualified(name: str) -> tuple[str, str]:
    """'provider.tool' → ('provider', 'tool')."""
    if "." not in name:
        raise UnknownProviderError(
            f"Tool name {name!r} is not qualified. Expected 'provider.tool'."
        )
    provider, _, tool = name.partition(".")
    return provider, tool


async def resolve_tools_for_node(node: AgentNode, context: BuildContext) -> list[BaseTool]:
    """Given an Agent node, return the list of BaseTool instances to bind.

    Phase 2: consumes `selectedTools` as `provider.tool` strings.
    Phase 3: additionally resolves `mcpServerIds` via McpToolProvider.
    """
    out: list[BaseTool] = []

    for qualified_name in node.data.selected_tools:
        provider_name, tool_name = _split_qualified(qualified_name)
        provider = get_provider(provider_name)
        out.append(await provider.build_tool(tool_name, context))

    if node.data.mcp_server_ids:
        raise NotImplementedError(
            f"MCP tool resolution lands in Phase 3 — server_ids={node.data.mcp_server_ids}"
        )

    return out


__all__ = [
    "UnknownProviderError",
    "UnknownToolError",
    "get_provider",
    "list_providers",
    "register_tool_provider",
    "resolve_tools_for_node",
]
