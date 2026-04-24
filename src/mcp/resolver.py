"""MCP server resolution — per ADR-0010, runs server-side at invocation time.

Given an Agent node's mcp_server_ids (or a single server_id + tool_name for
the `mcp` node executor), fetches McpServer rows from Prisma, instantiates
McpToolProvider, and returns the list of LangChain BaseTool instances.

See Phase 3a spec §10.
"""

from typing import Any

from langchain_core.tools import BaseTool

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.mcp.base import McpToolProvider
from src.tools.base import BuildContext


class McpServerNotFound(LookupError):
    """Raised when an mcp_server_id references a row that doesn't exist."""


class McpPermissionError(PermissionError):
    """Raised when a user tries to use an MCP server they don't own and isn't shared."""


def _user_can_use(user_id: str | None, server: Any) -> bool:
    """Owner always allowed; shared servers allowed for any user_id.

    Phase 3a: no OAuth, so the service-account token fallback isn't needed.
    Phase 3b expands this with real OAuth token lookup.
    """
    if server.userId == user_id:
        return True
    return bool(server.isShared)


async def resolve_mcp_tools_for_node(
    node: AgentNode,
    context: BuildContext,
    db: Any,
) -> list[BaseTool]:
    """Return BaseTool instances for every tool on every MCP server referenced by node."""
    server_ids = list(node.data.mcp_server_ids)
    if not server_ids:
        return []

    out: list[BaseTool] = []
    for server_id in server_ids:
        server = await db.mcpserver.find_unique(where={"id": server_id})
        if server is None:
            raise McpServerNotFound(f"MCP server {server_id!r} not found")
        if not _user_can_use(context.user_id, server):
            raise McpPermissionError(
                f"User {context.user_id!r} cannot use MCP server {server_id!r} "
                f"(owner={server.userId!r}, isShared={server.isShared})"
            )

        provider = McpToolProvider(server, db=db, user_id=context.user_id)
        # Build from cached defs so each tool is bound with its real
        # inputSchema — the LLM needs this to pass required args like
        # firecrawl_agent's `prompt`.
        for td in await provider.tools():
            out.append(provider.build_tool_from_def(td))
    return out


async def resolve_mcp_tools_by_names(
    mcp_server_id: str,
    tool_names: list[str],
    user_id: str | None,
    db: Any,
) -> list[BaseTool]:
    """Return BaseTool instances for the named tools on a single MCP server.

    Used by the `mcp` node's agent mode: the designer picks a server and a
    subset of its tools via the Designer panel's multi-select.  An empty
    `tool_names` list returns ALL of the server's tools so designers can
    start by just saying "use this MCP server" and iterate on the prompt.

    One tools/list round-trip fetches every definition; each tool is then
    bound with its real inputSchema so the LLM knows what args to pass.
    """
    server = await db.mcpserver.find_unique(where={"id": mcp_server_id})
    if server is None:
        raise McpServerNotFound(f"MCP server {mcp_server_id!r} not found")
    if not _user_can_use(user_id, server):
        raise McpPermissionError(f"User {user_id!r} cannot use MCP server {mcp_server_id!r}")

    provider = McpToolProvider(server, db=db, user_id=user_id)
    all_defs = await provider.tools()

    if not tool_names:
        return [provider.build_tool_from_def(td) for td in all_defs]

    by_name = {td.name: td for td in all_defs}
    out: list[BaseTool] = []
    for name in tool_names:
        td = by_name.get(name)
        if td is None:
            # Selected tool isn't advertised by the server — skip rather
            # than abort so a stale config doesn't take down the whole
            # node, and log for the designer to notice.
            continue
        out.append(provider.build_tool_from_def(td))
    return out


async def resolve_single_mcp_tool(
    mcp_server_id: str,
    tool_name: str,
    user_id: str | None,
    db: Any,
) -> tuple[McpToolProvider, BaseTool]:
    """For the `mcp` node deterministic-mode executor: fetch server, build
    ONE tool by name with its real schema."""
    server = await db.mcpserver.find_unique(where={"id": mcp_server_id})
    if server is None:
        raise McpServerNotFound(f"MCP server {mcp_server_id!r} not found")
    if not _user_can_use(user_id, server):
        raise McpPermissionError(f"User {user_id!r} cannot use MCP server {mcp_server_id!r}")

    provider = McpToolProvider(server, db=db, user_id=user_id)
    tool = await provider.build_tool(
        tool_name,
        BuildContext(
            node=None,  # pyright: ignore[reportArgumentType]
            state=initial_state(),
            user_id=user_id,
        ),
    )
    return provider, tool


__all__ = [
    "McpPermissionError",
    "McpServerNotFound",
    "resolve_mcp_tools_by_names",
    "resolve_mcp_tools_for_node",
    "resolve_single_mcp_tool",
]
