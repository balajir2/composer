"""JiraExecutor — the `jira` node type.

Two operation modes, selected by `node.data.operation`:

- `"agent"` (default, `_run_agent`) — the node reads domain/email/api_token
  from its own data, injects them into state, then runs an agentic tool-
  calling loop against Jira's REST API tools via the JiraProvider. The LLM
  decides which tool(s) to call and when to stop.
- `"extract"` (`_run_extract`) — deterministic, no LLM involved. Runs the
  node's configured JQL through Jira's search endpoint, paginating until
  `max_issues` is reached or all matching issues are fetched, with bounded
  retry on 429/5xx responses. Output is raw per-issue field JSON.

Credentials flow (agent mode): node data → state.variables →
JiraProvider.build_tool(). No env vars required (but accepted as fallback).
Extract mode talks to the Jira REST API directly via build_headers/build_url
and does not go through JiraProvider or state.variables.

Structured action outcomes (P0-2): the Jira tools in
src/tools/providers/jira.py signal failure by returning a string prefixed
"Error: " rather than raising — a failed create-issue call is otherwise
indistinguishable from a successful one to the agentic loop, and an LLM
response with zero tool calls (e.g. "I need more information") can
complete the node without having done anything. `action_policy` makes
that distinction explicit and enforceable.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.engine.context import get_current_langsmith
from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode, JiraNode, decrypt_jira_api_token
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.tools.base import BuildContext
from src.tools.providers.jira import JiraProvider, build_headers, build_url
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

_EXTRACT_PAGE_SIZE = 100
_EXTRACT_MAX_RETRY_ATTEMPTS = 3


class JiraExtractConfigError(RuntimeError):
    """Raised when operation='extract' is missing required config (domain/email/apiToken/jql)."""


class JiraExtractHttpError(RuntimeError):
    """Raised when the Jira search REST call fails after exhausting retries, or fails
    with a non-retryable 4xx status."""


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
        if self.node.data.operation == "extract":
            return await self._run_extract(state)
        return await self._run_agent(state)

    async def _run_extract(self, state: WorkflowStateDict) -> dict[str, Any]:
        domain = self.node.data.domain or ""
        email = self.node.data.email or ""
        api_token = decrypt_jira_api_token(self.node.data.api_token or "")
        jql = substitute(self.node.data.jql or "", state)
        required = [("domain", domain), ("email", email), ("apiToken", api_token), ("jql", jql)]
        missing = [name for name, val in required if not val]
        if missing:
            raise JiraExtractConfigError(
                f"jira node {self.node.id!r}: operation='extract' is missing required "
                f"config: {', '.join(missing)}."
            )
        fields = self.node.data.fields or []
        expand_changelog = self.node.data.expand_changelog
        max_issues = self.node.data.max_issues

        issues: list[dict[str, Any]] = []
        start_at = 0
        total: int | None = None
        headers = build_headers(email, api_token)
        url = build_url(domain, "search")

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            while True:
                remaining = max_issues - len(issues)
                if remaining <= 0:
                    break
                body: dict[str, Any] = {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": min(_EXTRACT_PAGE_SIZE, remaining),
                    "fields": fields,
                }
                if expand_changelog:
                    body["expand"] = ["changelog"]
                resp = await _post_search_with_retry(client, url, headers, body)
                data = resp.json()
                page_issues = data.get("issues", [])
                total = data.get("total", len(page_issues))
                for iss in page_issues:
                    entry: dict[str, Any] = {
                        "key": iss.get("key"),
                        "fields": iss.get("fields", {}),
                    }
                    if expand_changelog:
                        entry["changelog"] = iss.get("changelog", {})
                    issues.append(entry)
                start_at += len(page_issues)
                if not page_issues or (total is not None and start_at >= total):
                    break

        truncated = total is not None and len(issues) < total
        output = {
            "issues": issues,
            "total": total if total is not None else len(issues),
            "fetched": len(issues),
            "truncated": truncated,
        }
        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"jql": jql, "fields": fields},
                    "output": output,
                }
            },
        }

    async def _run_agent(self, state: WorkflowStateDict) -> dict[str, Any]:
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


async def _post_search_with_retry(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any]
) -> httpx.Response:
    last_error: JiraExtractHttpError | None = None
    for attempt in range(_EXTRACT_MAX_RETRY_ATTEMPTS):
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            last_error = JiraExtractHttpError(f"Jira search request failed: {exc}")
            if attempt < _EXTRACT_MAX_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = JiraExtractHttpError(
                f"Jira search failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )
            if attempt < _EXTRACT_MAX_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
            continue
        if resp.status_code >= 400:
            raise JiraExtractHttpError(
                f"Jira search failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )
        return resp
    assert last_error is not None
    raise last_error


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


__all__ = [
    "JiraActionPolicyError",
    "JiraExecutor",
    "JiraExtractConfigError",
    "JiraExtractHttpError",
    "JiraMaxIterationsError",
]
