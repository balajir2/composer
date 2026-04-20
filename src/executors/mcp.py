"""McpExecutor — the `mcp` node type.

Standalone node that calls ONE tool on ONE MCP server, with no LLM in
the loop. Useful for deterministic workflows like "scrape this URL,
then hand off to an agent" (start → mcp → agent → end).

See Phase 3a spec §11.
"""

import logging
from typing import Any

from src.engine.context import get_current_db
from src.engine.state import WorkflowStateDict
from src.engine.workflow import McpNode
from src.executors.base import register_executor
from src.mcp.resolver import resolve_single_mcp_tool
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)


@register_executor("mcp")
class McpExecutor:
    def __init__(self, node: McpNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        if not self.node.data.mcp_server_id:
            raise ValueError(f"mcp node {self.node.id!r} requires data.mcpServerId to be set")
        if not self.node.data.tool_name:
            raise ValueError(f"mcp node {self.node.id!r} requires data.toolName to be set")

        raw_args = self.node.data.arguments or {}
        resolved_args: dict[str, Any] = {}
        for k, v in raw_args.items():
            if isinstance(v, str):
                resolved_args[k] = substitute(v, state)
            else:
                resolved_args[k] = v

        db = get_current_db()
        if db is None:
            raise RuntimeError(
                "McpExecutor requires src.engine.context.set_current_db to be called "
                "before the compiled graph runs. LangGraphExecutor.run does this."
            )

        _, tool = await resolve_single_mcp_tool(
            self.node.data.mcp_server_id,
            self.node.data.tool_name,
            user_id=None,  # Phase 7 wires real user_id
            db=db,
        )
        result = await tool.ainvoke(resolved_args)

        return {
            "variables": {"lastOutput": result},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": resolved_args,
                    "output": result,
                }
            },
        }


__all__ = ["McpExecutor"]
