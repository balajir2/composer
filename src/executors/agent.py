"""Agent node executor — full agentic loop.

Ports OAB's agent.ts:366-506 / 893-1015 / 1111-1223 while-loop into
Python, using LangChain's unified bind_tools() so provider-specific
tool format dispatch is handled internally.

See spec §10; ADRs 0006-0008.
"""

import asyncio
import json
import logging
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.engine.context import get_current_db, get_current_langsmith
from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.llm.structured_output import structured_invoke
from src.tools import registry as _registry
from src.tools.base import BuildContext
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"
DEFAULT_MAX_ITERATIONS = 10  # matches OAB
ABSOLUTE_MAX_ITERATIONS = 100  # hard clamp on user-configured values


class MaxIterationsExceededError(RuntimeError):
    """Raised when the agentic loop hits MAX_ITERATIONS without converging."""


def unwrap_message_content(content: Any) -> str:
    """Flatten LangChain `BaseMessage.content` into a clean string.

    Anthropic and other providers return content blocks
    (`[{type: "text", text: ...}, {type: "thinking", ...}, ...]`) when
    extended thinking, signed-tool-use, or vision is in play.  The
    pre-fix code stringified the whole list, leaking
    ``[{'type': 'text', 'text': '...', 'extras': {...}}]`` into the
    workflow output and forcing every external caller to parse a
    Python repr.

    Walk the list, keep only `text`-type blocks, join their text
    (preserving order), and return.  Non-text blocks (thinking,
    tool_use, image) are dropped because callers asked for the
    assistant's textual answer; the metadata isn't part of that.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in cast("list[Any]", content):
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                block = cast("dict[str, Any]", block)
                if block.get("type") == "text":
                    # Both LangChain (`type` field) and OpenAI vision
                    # (`type: "text"`) wrap text the same way.
                    text_value = block.get("text", "")
                    if isinstance(text_value, str):
                        parts.append(text_value)
        return "\n".join(p for p in parts if p)
    # Anything else (None, dict, etc.) — fall back to str() so we
    # never propagate a non-string to a downstream node that's
    # expecting text.
    return str(content) if content is not None else ""


class ModelUnavailableError(RuntimeError):
    """Raised when an agent's configured model has been marked unavailable.

    Admin clicks Verify in the LLM models console; if the provider 404s
    (or returns model_not_found), the row is stamped `unavailable` and
    `enabled=False`.  Workflows that already reference the model by name
    still try to use it on next run — this guard fails fast with a clear
    message instead of letting the request reach Google/OpenAI and time
    out with an opaque stack trace.
    """


async def _ensure_model_available(model_string: str) -> None:
    """Look up `provider/model_id` in the LlmModel catalog.  If it's
    been verified as unavailable, raise immediately so the failure
    surfaces in the run trace as 'pick a different model' rather than
    a 404 from Google buried in a tenacity stack."""
    if "/" not in model_string:
        return
    provider, _, model_name = model_string.partition("/")
    db = get_current_db()
    if db is None:
        return
    row = await db.llmmodel.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"provider_modelId": {"provider": provider, "modelId": model_name}}
    )
    if row is None:
        return
    if row.verificationStatus == "unavailable":
        raise ModelUnavailableError(
            f"Model {model_string!r} is marked unavailable: "
            f"{row.verificationMessage or 'see Admin → LLM models for details'}. "
            "Update the workflow's agent node to use a different model, or "
            "re-verify the row in Admin → LLM models if the provider has restored access."
        )


@register_executor("agent")
class AgentExecutor:
    def __init__(self, node: AgentNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_instructions = self.node.data.instructions or ""
        instructions = substitute(raw_instructions, state)

        messages = self._build_messages(instructions, state)

        model_string = self.node.data.model or DEFAULT_MODEL
        # Pre-flight: refuse fast if the model has been verified as
        # unavailable (e.g. Google retired gemini-2.0-flash for new
        # keys).  Without this, the request reaches the provider and
        # surfaces as a 404 buried in tenacity retries.
        await _ensure_model_available(model_string)

        chat_model = _providers.build_chat_model(
            model_string,
            langsmith_config=get_current_langsmith(),
        )

        # Phase 7a: user_id flows from WorkflowExecution.userId via state;
        # fallback to 'dev' for pre-7a checkpoints still in flight.
        user_id = state.get("user_id") or "dev"
        tools = await _registry.resolve_tools_for_node(
            self.node,
            BuildContext(
                node=self.node,
                state=state,
                user_id=user_id,
                db=get_current_db(),
            ),
        )

        final_text = await self._agentic_loop(chat_model, messages, tools)

        output = await self._format_output(chat_model, instructions, final_text)

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

    def _build_messages(self, instructions: str, state: WorkflowStateDict) -> list[BaseMessage]:
        msgs: list[BaseMessage] = []
        if self.node.data.include_chat_history and state["chat_history"]:
            for m in state["chat_history"]:
                role = m.get("role")
                if role == "user":
                    msgs.append(HumanMessage(content=m.get("content", "")))
                elif role == "assistant":
                    msgs.append(AIMessage(content=m.get("content", "")))
        msgs.append(HumanMessage(content=instructions))
        return msgs

    async def _agentic_loop(
        self,
        chat_model: BaseChatModel,
        messages: list[BaseMessage],
        tools: list[BaseTool],
    ) -> str:
        """Drive the LLM + tools until a turn produces text with no tool_calls.
        Caps at the node's configured max_iterations (default 10, hard
        clamp 100).  Returns the final assistant text."""
        bound_model = (
            chat_model.bind_tools(tools)  # pyright: ignore[reportUnknownMemberType]
            if tools
            else chat_model
        )
        current: list[BaseMessage] = list(messages)
        tools_by_name = {t.name: t for t in tools}
        cap = min(
            self.node.data.max_iterations or DEFAULT_MAX_ITERATIONS,
            ABSOLUTE_MAX_ITERATIONS,
        )

        for _ in range(cap + 1):
            response = await bound_model.ainvoke(current)  # pyright: ignore[reportUnknownMemberType]
            tool_calls = getattr(response, "tool_calls", [])

            if not tool_calls:
                # Extract clean text — handles both plain strings and
                # provider content-block arrays (Anthropic extended
                # thinking, vision, etc.).  Without this the workflow
                # output would contain the Python repr of a list of
                # dicts, which downstream nodes and external callers
                # would have to parse.
                if hasattr(response, "content"):
                    return unwrap_message_content(
                        response.content  # pyright: ignore[reportUnknownMemberType]
                    )
                return str(response)

            # Execute all tool calls in parallel, append results as ToolMessages.
            tool_results = await asyncio.gather(
                *[self._run_tool(tools_by_name, tc) for tc in tool_calls]
            )
            current = [*current, response, *tool_results]

        raise MaxIterationsExceededError(
            f"Agent {self.node.id!r} hit max_iterations={cap} without producing a "
            "final text response. Bump the 'Max iterations' field on the node or "
            "refine the prompt so the LLM converges faster."
        )

    async def _run_tool(
        self, tools_by_name: dict[str, BaseTool], tool_call: dict[str, Any]
    ) -> ToolMessage:
        name = tool_call.get("name", "")
        tool_id = tool_call.get("id", "")
        args = tool_call.get("args", {})
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
            logger.exception("Tool %s raised", name)
            return ToolMessage(
                content=f"Error: tool {name!r} raised {type(exc).__name__}: {exc}",
                tool_call_id=tool_id,
            )
        return ToolMessage(content=str(result), tool_call_id=tool_id)

    async def _format_output(
        self, chat_model: BaseChatModel, instructions: str, final_text: str
    ) -> Any:
        output_format = (self.node.data.output_format or "Text").lower()
        if output_format == "json":
            # Re-invoke with json_mode on the assistant's produced text so the
            # parse is provider-native rather than prompt-string fragile.
            messages: list[BaseMessage] = [
                HumanMessage(
                    content=(
                        f"{instructions}\n\nYour previous draft answer was:\n"
                        f"{final_text}\n\nReturn JSON only."
                    )
                ),
            ]
            schema = self.node.data.json_schema
            result = await structured_invoke(
                chat_model,
                messages,
                schema=schema,
                json_mode=schema is None,
            )
            # structured_invoke may return a raw AIMessage when the provider's
            # with_structured_output chain yields one instead of a dict/str.
            # Flatten content blocks first (Anthropic extended thinking can
            # land here too), then attempt json.loads so callers always get
            # a dict / clean string.
            if isinstance(result, AIMessage):
                text = unwrap_message_content(
                    result.content  # pyright: ignore[reportUnknownMemberType]
                )
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    return text
            return result
        return final_text


__all__ = ["AgentExecutor", "MaxIterationsExceededError"]
