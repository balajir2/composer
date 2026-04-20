"""Agent node executor.

Non-tool path only in Task 9. Task 10 adds the agentic loop (tool
execution). See spec §10; ADRs 0006-0008.
"""

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.llm.structured_output import structured_invoke
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-3-5-haiku-latest"


@register_executor("agent")
class AgentExecutor:
    """Single-turn Agent (no tools). Tool-loop lands in Task 10."""

    def __init__(self, node: AgentNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_instructions = self.node.data.instructions or ""
        instructions = substitute(raw_instructions, state)

        messages = self._build_messages(instructions, state)

        chat_model = _providers.build_chat_model(self.node.data.model or DEFAULT_MODEL)

        output = await self._invoke(chat_model, messages)

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
        """Messages: optionally prepend chat history, then a HumanMessage."""
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

    async def _invoke(self, chat_model: BaseChatModel, messages: list[BaseMessage]) -> Any:
        """Non-tool invocation; outputFormat routes through structured_invoke."""
        output_format = (self.node.data.output_format or "Text").lower()
        if output_format == "json":
            result = await structured_invoke(chat_model, messages, json_mode=True)
            # structured_invoke may return a raw AIMessage when the provider's
            # with_structured_output chain yields one instead of a dict/str.
            # Extract content and json.loads it so callers always get a dict.
            if isinstance(result, AIMessage):
                content = result.content
                if isinstance(content, str):
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return content
                return content
            return result
        return await structured_invoke(chat_model, messages)


__all__ = ["AgentExecutor"]
