"""Agent node executor — full agentic loop.

Ports OAB's agent.ts:366-506 / 893-1015 / 1111-1223 while-loop into
Python, using LangChain's unified bind_tools() so provider-specific
tool format dispatch is handled internally.

See spec §10; ADRs 0006-0008.
"""

import asyncio
import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.llm.structured_output import structured_invoke
from src.tools import registry as _registry
from src.tools.base import BuildContext
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-3-5-haiku-latest"
MAX_ITERATIONS = 10  # matches OAB


class MaxIterationsExceededError(RuntimeError):
    """Raised when the agentic loop hits MAX_ITERATIONS without converging."""


@register_executor("agent")
class AgentExecutor:
    def __init__(self, node: AgentNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_instructions = self.node.data.instructions or ""
        instructions = substitute(raw_instructions, state)

        messages = self._build_messages(instructions, state)

        chat_model = _providers.build_chat_model(self.node.data.model or DEFAULT_MODEL)

        tools = await _registry.resolve_tools_for_node(
            self.node,
            BuildContext(node=self.node, state=state),
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
        Caps at MAX_ITERATIONS iterations. Returns the final assistant text."""
        bound_model = chat_model.bind_tools(tools) if tools else chat_model
        current: list[BaseMessage] = list(messages)
        tools_by_name = {t.name: t for t in tools}

        for _ in range(MAX_ITERATIONS + 1):
            response = await bound_model.ainvoke(current)
            tool_calls = getattr(response, "tool_calls", [])

            if not tool_calls:
                content = response.content if hasattr(response, "content") else str(response)
                return content if isinstance(content, str) else str(content)

            # Execute all tool calls in parallel, append results as ToolMessages.
            tool_results = await asyncio.gather(
                *[self._run_tool(tools_by_name, tc) for tc in tool_calls]
            )
            current = [*current, response, *tool_results]

        raise MaxIterationsExceededError(
            f"Agent {self.node.id!r} hit MAX_ITERATIONS={MAX_ITERATIONS} "
            f"without producing a final text response."
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
            result = await tool.ainvoke(args)  # pyright: ignore[reportArgumentType]
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
            # Unwrap and json.loads so callers always get a dict.
            if isinstance(result, AIMessage):
                content = result.content
                if isinstance(content, str):
                    try:
                        return json.loads(content)
                    except (json.JSONDecodeError, ValueError):
                        return content
                return content
            return result
        return final_text


__all__ = ["AgentExecutor", "MaxIterationsExceededError"]
