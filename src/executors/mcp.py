"""McpExecutor — the `mcp` node type.

Two modes:

1. **Agent mode** (preferred, used when `data.instructions` is set):
   The node binds a chat model to a subset of the MCP server's tools
   (`data.selectedToolNames`) and runs an agentic loop against the prompt.
   Model picks which tools to call.

2. **Deterministic mode** (legacy, used when `data.toolName` is set and
   `data.instructions` is not): calls ONE named tool on ONE server with
   the resolved `data.arguments` dict.

See Phase 3a spec §11 (deterministic mode) and the 2026-04-24 UX update
that added agent mode so designers can pick a server + multiple tools +
a prompt directly on the MCP node.
"""

import asyncio
import logging
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.engine.context import get_current_db, get_current_langsmith
from src.engine.state import WorkflowStateDict
from src.engine.workflow import McpNode
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.mcp.resolver import resolve_mcp_tools_by_names, resolve_single_mcp_tool
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"
DEFAULT_MAX_ITERATIONS = 10
# Hard upper bound — even user-configured values clamp here to avoid
# runaway tool-call loops on a misbehaving LLM.
ABSOLUTE_MAX_ITERATIONS = 100


class McpMaxIterationsError(RuntimeError):
    """Raised when the MCP agentic loop runs past MAX_ITERATIONS."""


@register_executor("mcp")
class McpExecutor:
    def __init__(self, node: McpNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        if not self.node.data.mcp_server_id:
            raise ValueError(f"mcp node {self.node.id!r} requires data.mcpServerId to be set")

        # Agent mode wins when instructions is present (the new default UX).
        if self.node.data.instructions:
            return await self._run_agent_mode(state)
        return await self._run_deterministic_mode(state)

    # ─── Agent mode ─────────────────────────────────────────────────────

    async def _run_agent_mode(self, state: WorkflowStateDict) -> dict[str, Any]:
        instructions = substitute(self.node.data.instructions or "", state)
        db = _require_db()
        user_id = state.get("user_id") or "dev"

        # Scope the tool set to what the user selected (falls back to all tools
        # on the server if nothing was picked — lets designers get started
        # without having to know tool names upfront).
        assert self.node.data.mcp_server_id is not None
        tools = await resolve_mcp_tools_by_names(
            self.node.data.mcp_server_id,
            self.node.data.selected_tool_names,
            user_id=user_id,
            db=db,
        )

        chat_model = _providers.build_chat_model(
            self.node.data.model or DEFAULT_MODEL,
            langsmith_config=get_current_langsmith(),
        )

        messages: list[BaseMessage] = [HumanMessage(content=instructions)]
        cap = min(
            self.node.data.max_iterations or DEFAULT_MAX_ITERATIONS,
            ABSOLUTE_MAX_ITERATIONS,
        )
        final_text = await _agentic_loop(self.node.id, chat_model, messages, tools, cap)

        return {
            "variables": {"lastOutput": final_text},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": instructions,
                    "output": final_text,
                }
            },
        }

    # ─── Deterministic mode (legacy single-tool call) ────────────────────

    async def _run_deterministic_mode(self, state: WorkflowStateDict) -> dict[str, Any]:
        if not self.node.data.tool_name:
            raise ValueError(
                f"mcp node {self.node.id!r} requires either data.instructions "
                "(agent mode) or data.toolName (deterministic mode) to be set."
            )

        raw_args = self.node.data.arguments or {}
        resolved_args: dict[str, Any] = {}
        for k, v in raw_args.items():
            resolved_args[k] = substitute(v, state) if isinstance(v, str) else v

        db = _require_db()
        user_id = state.get("user_id") or "dev"
        assert self.node.data.mcp_server_id is not None
        _, tool = await resolve_single_mcp_tool(
            self.node.data.mcp_server_id,
            self.node.data.tool_name,
            user_id=user_id,
            db=db,
        )
        result = await tool.ainvoke(resolved_args)  # pyright: ignore[reportUnknownMemberType]

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


# ─── helpers (module-level so they're trivially unit-testable) ─────────


def _require_db() -> Any:
    db = get_current_db()
    if db is None:
        raise RuntimeError(
            "McpExecutor requires src.engine.context.set_current_db to be "
            "called before the compiled graph runs. LangGraphExecutor.run does this."
        )
    return db


async def _agentic_loop(
    node_id: str,
    chat_model: BaseChatModel,
    messages: list[BaseMessage],
    tools: list[BaseTool],
    max_iterations: int,
) -> str:
    """LLM + tool-call loop.  Mirrors the Agent executor's pattern but kept
    local so the MCP node stays self-contained and unit-testable."""
    bound_model = (
        chat_model.bind_tools(tools)  # pyright: ignore[reportUnknownMemberType]
        if tools
        else chat_model
    )
    current: list[BaseMessage] = list(messages)
    tools_by_name = {t.name: t for t in tools}

    for _ in range(max_iterations + 1):
        response = await bound_model.ainvoke(current)  # pyright: ignore[reportUnknownMemberType]
        tool_calls: list[dict[str, Any]] = getattr(response, "tool_calls", []) or []
        if not tool_calls:
            content = cast(
                "str | list[Any]",
                response.content  # pyright: ignore[reportUnknownMemberType]
                if hasattr(response, "content")
                else str(response),
            )
            return content if isinstance(content, str) else str(content)
        tool_results = await asyncio.gather(*[_run_tool(tools_by_name, tc) for tc in tool_calls])
        current = [*current, response, *tool_results]

    raise McpMaxIterationsError(
        f"MCP node {node_id!r} hit max_iterations={max_iterations} without producing a "
        "final text response. Bump the 'Max iterations' field on the node or refine "
        "the prompt so the LLM converges faster."
    )


async def _run_tool(tools_by_name: dict[str, BaseTool], tool_call: dict[str, Any]) -> ToolMessage:
    name = tool_call.get("name", "")
    tool_id = tool_call.get("id", "")
    args = tool_call.get("args", {})
    # Log the tool invocation so designers can see in the server log whether
    # the LLM is passing the right params.  Particularly useful for MCP
    # tools whose servers do strict schema validation (firecrawl, highspot).
    logger.info("MCP tool call: name=%s args=%r", name, args)
    if name not in tools_by_name:
        return ToolMessage(
            content=f"Error: tool {name!r} is not registered.",
            tool_call_id=tool_id,
        )
    tool = tools_by_name[name]
    try:
        result = await tool.ainvoke(  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
            args
        )
    except Exception as exc:
        logger.exception("MCP tool %s raised", name)
        return ToolMessage(
            content=f"Error: tool {name!r} raised {type(exc).__name__}: {exc}",
            tool_call_id=tool_id,
        )
    return ToolMessage(content=str(result), tool_call_id=tool_id)


# Back-compat re-exports so other modules that imported AIMessage etc.
# through this file (none today, but possible) don't break.
__all__ = ["AIMessage", "McpExecutor", "McpMaxIterationsError"]
