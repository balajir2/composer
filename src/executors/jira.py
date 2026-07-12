"""JiraExecutor — the `jira` node type.

Agent-mode only: the node reads domain/email/api_token from its own data,
injects them into state, then runs an agentic loop against Jira's REST API
tools via the JiraProvider.

Credentials flow: node data → state.variables → JiraProvider.build_tool().
No env vars required (but accepted as fallback).
"""

import asyncio
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.engine.context import get_current_langsmith
from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode, JiraNode, decrypt_jira_api_token
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.tools.base import BuildContext
from src.tools.providers.jira import JiraProvider
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"
DEFAULT_MAX_ITERATIONS = 10
ABSOLUTE_MAX_ITERATIONS = 100


class JiraMaxIterationsError(RuntimeError):
    """Raised when the Jira agentic loop runs past max_iterations."""


@register_executor("jira")
class JiraExecutor:
    def __init__(self, node: JiraNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        instructions = substitute(self.node.data.instructions or "", state)

        # Inject per-node credentials into state so the JiraProvider can
        # read them from state.variables. The node's own configured value
        # always wins when present (P0-7) — state.variables can contain
        # anything an external caller passed via POST /executions or
        # /api/run/{slug} (src/executors/start.py spreads caller input
        # into variables wholesale), so letting a state value silently
        # override the node's configured domain/email/token would let a
        # caller redirect ticket creation to a Jira tenant/credential of
        # their choosing. The state fallback only applies when the node
        # itself has nothing configured — the legitimate case of a
        # workflow relying entirely on an earlier Set State node or an
        # admin-provided default.
        variables = dict(state.get("variables") or {})
        if self.node.data.domain:
            variables["jira_domain"] = self.node.data.domain
        if self.node.data.email:
            variables["jira_email"] = self.node.data.email
        if self.node.data.api_token:
            variables["jira_api_token"] = decrypt_jira_api_token(self.node.data.api_token)

        provider = JiraProvider()
        tools: list[BaseTool] = []  # Use all Jira tools — model picks the right one
        for td in await provider.tools():
            node_for_ctx = AgentNode.model_validate(
                {
                    "id": self.node.id,
                    "type": "agent",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": self.node.data.label or "jira"},
                }
            )
            ctx = BuildContext(node=node_for_ctx, state={**state, "variables": variables})
            tool = await provider.build_tool(td.name, ctx)
            tools.append(tool)

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


async def _agentic_loop(
    node_id: str,
    chat_model: BaseChatModel,
    messages: list[BaseMessage],
    tools: list[BaseTool],
    max_iterations: int,
) -> str:
    bound_model = chat_model.bind_tools(tools) if tools else chat_model
    current: list[BaseMessage] = list(messages)
    tools_by_name = {t.name: t for t in tools}

    for _ in range(max_iterations + 1):
        response = await bound_model.ainvoke(current)
        tool_calls: list[dict[str, Any]] = getattr(response, "tool_calls", []) or []
        if not tool_calls:
            content = response.content if hasattr(response, "content") else str(response)
            return content if isinstance(content, str) else str(content)
        tool_results = await asyncio.gather(*[_run_tool(tools_by_name, tc) for tc in tool_calls])
        current = [*current, response, *tool_results]

    raise JiraMaxIterationsError(
        f"Jira node {node_id!r} hit max_iterations={max_iterations} without producing a "
        "final text response."
    )


async def _run_tool(tools_by_name: dict[str, BaseTool], tool_call: dict[str, Any]) -> ToolMessage:
    name = tool_call.get("name", "")
    tool_id = tool_call.get("id", "")
    args = tool_call.get("args", {})
    logger.info("Jira tool call: name=%s args=%r", name, args)
    if name not in tools_by_name:
        return ToolMessage(
            content=f"Error: tool {name!r} is not registered.",
            tool_call_id=tool_id,
        )
    tool = tools_by_name[name]
    try:
        result = await tool.ainvoke(args)  # pyright: ignore[reportArgumentType]
    except Exception as exc:
        logger.exception("Jira tool %s raised", name)
        return ToolMessage(
            content=f"Error: tool {name!r} raised {type(exc).__name__}: {exc}",
            tool_call_id=tool_id,
        )
    return ToolMessage(content=str(result), tool_call_id=tool_id)


__all__ = ["JiraExecutor", "JiraMaxIterationsError"]
