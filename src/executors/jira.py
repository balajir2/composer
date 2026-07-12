"""JiraExecutor — the `jira` node type.

Agent-mode only: the node reads domain/email/api_token from its own data,
injects them into state, then runs an agentic loop against Jira's REST API
tools via the JiraProvider.

Credentials flow: node data → state.variables → JiraProvider.build_tool().
No env vars required (but accepted as fallback).

Structured action outcomes (P0-2): the Jira tools in
src/tools/providers/jira.py signal failure by returning a string prefixed
"Error: " rather than raising — a failed create-issue call is otherwise
indistinguishable from a successful one to the agentic loop, and an LLM
response with zero tool calls (e.g. "I need more information") can
complete the node without having done anything. `action_policy` makes
that distinction explicit and enforceable.
"""

import logging
import re
from dataclasses import dataclass
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

# Jira tools return their own failures as `"Error: ..."` strings rather
# than raising (src/tools/providers/jira.py) — this is the only reliable
# signal a wrapping loop has to distinguish success from failure.
_ERROR_PREFIX = "Error:"
# jira_create_issue's success string: "Created Jira issue PROJ-42: https://..."
_CREATED_ISSUE_PATTERN = re.compile(r"^Created Jira issue (\S+):")


class JiraMaxIterationsError(RuntimeError):
    """Raised when the Jira agentic loop runs past max_iterations."""


class JiraActionPolicyError(RuntimeError):
    """Raised when the node's configured action_policy isn't satisfied —
    e.g. require_tool_call with a text-only response, or
    require_successful_tool_call with too few successful calls."""


@dataclass
class ToolCallRecord:
    name: str
    ok: bool
    summary: str
    resource_id: str | None = None


def _sanitize_args_for_logging(args: dict[str, Any]) -> list[str]:
    """Tool args can carry full BRD/comment/description text — never log
    values, only which keys were supplied."""
    return sorted(args.keys())


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
        final_text, records = await _agentic_loop(self.node.id, chat_model, messages, tools, cap)

        success_count = sum(1 for r in records if r.ok)
        failure_count = len(records) - success_count
        created_issue_keys = [r.resource_id for r in records if r.ok and r.resource_id]

        policy = self.node.data.action_policy
        if policy in ("require_tool_call", "require_successful_tool_call") and not records:
            raise JiraActionPolicyError(
                f"jira node {self.node.id!r}: action_policy={policy!r} requires at least "
                f"one tool call, but the model responded with no tool call at all."
            )
        if policy == "require_successful_tool_call":
            minimum = max(1, self.node.data.minimum_successful_calls)
            if success_count < minimum:
                raise JiraActionPolicyError(
                    f"jira node {self.node.id!r}: action_policy=require_successful_tool_call "
                    f"requires at least {minimum} successful tool call(s), but only "
                    f"{success_count} of {len(records)} succeeded."
                )

        output: dict[str, Any] = {
            "text": final_text,
            "toolCallCount": len(records),
            "successCount": success_count,
            "failureCount": failure_count,
            "createdIssueKeys": created_issue_keys,
        }

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": instructions,
                    "output": output,
                }
            },
        }


async def _agentic_loop(
    node_id: str,
    chat_model: BaseChatModel,
    messages: list[BaseMessage],
    tools: list[BaseTool],
    max_iterations: int,
) -> tuple[str, list[ToolCallRecord]]:
    bound_model = chat_model.bind_tools(tools) if tools else chat_model
    current: list[BaseMessage] = list(messages)
    tools_by_name = {t.name: t for t in tools}
    all_records: list[ToolCallRecord] = []

    for _ in range(max_iterations + 1):
        response = await bound_model.ainvoke(current)
        tool_calls: list[dict[str, Any]] = getattr(response, "tool_calls", []) or []
        if not tool_calls:
            content = response.content if hasattr(response, "content") else str(response)
            return (content if isinstance(content, str) else str(content)), all_records
        # Mutating Jira calls run sequentially, not concurrently
        # (asyncio.gather previously) — dependency ordering within a
        # single model turn (e.g. create issue, then comment on it)
        # otherwise can't be relied on.
        results: list[ToolMessage] = []
        for tc in tool_calls:
            message, record = await _run_tool(tools_by_name, tc)
            results.append(message)
            all_records.append(record)
        current = [*current, response, *results]

    raise JiraMaxIterationsError(
        f"Jira node {node_id!r} hit max_iterations={max_iterations} without producing a "
        "final text response."
    )


async def _run_tool(
    tools_by_name: dict[str, BaseTool], tool_call: dict[str, Any]
) -> tuple[ToolMessage, ToolCallRecord]:
    name = tool_call.get("name", "")
    tool_id = tool_call.get("id", "")
    args = tool_call.get("args", {})
    logger.info("Jira tool call: name=%s arg_keys=%s", name, _sanitize_args_for_logging(args))
    if name not in tools_by_name:
        content = f"Error: tool {name!r} is not registered."
        return (
            ToolMessage(content=content, tool_call_id=tool_id),
            ToolCallRecord(name=name, ok=False, summary=content),
        )
    tool = tools_by_name[name]
    try:
        result = await tool.ainvoke(args)  # pyright: ignore[reportArgumentType]
    except Exception as exc:
        logger.exception("Jira tool %s raised", name)
        content = f"Error: tool {name!r} raised {type(exc).__name__}: {exc}"
        return (
            ToolMessage(content=content, tool_call_id=tool_id),
            ToolCallRecord(name=name, ok=False, summary=content),
        )
    text = str(result)
    ok = not text.startswith(_ERROR_PREFIX)
    resource_id: str | None = None
    if ok:
        match = _CREATED_ISSUE_PATTERN.match(text)
        if match:
            resource_id = match.group(1)
    return (
        ToolMessage(content=text, tool_call_id=tool_id),
        ToolCallRecord(name=name, ok=ok, summary=text[:500], resource_id=resource_id),
    )


__all__ = ["JiraActionPolicyError", "JiraExecutor", "JiraMaxIterationsError"]
