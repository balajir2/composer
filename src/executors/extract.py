"""extract node executor.

LLM structured-output extraction. Reuses Phase 2's `structured_invoke`
so the same JSON-mode / schema logic backs both Agent JSON output and
Extract nodes.

See Phase 4a spec §10.
"""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.engine.context import get_current_langsmith
from src.engine.state import WorkflowStateDict
from src.engine.workflow import ExtractNode
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.llm import structured_output as _so
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_EXTRACT_MODEL = "anthropic/claude-haiku-4-5-20251001"


class ExtractNodeError(RuntimeError):
    """Raised when extract receives empty input or the LLM response can't be parsed."""


def _coerce_to_dict(result: Any) -> Any:
    """Pull a dict out of structured_invoke's result (may be AIMessage)."""
    if isinstance(result, AIMessage):
        content = result.content
        if isinstance(content, str):
            try:
                return json.loads(content)
            except (json.JSONDecodeError, ValueError):
                return content
        return content
    return result


@register_executor("extract")
class ExtractExecutor:
    def __init__(self, node: ExtractNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        template = self.node.data.input_text or "{{lastOutput}}"
        input_text = substitute(template, state)
        if not input_text.strip():
            raise ExtractNodeError(f"extract node {self.node.id!r} has empty input")

        chat_model = _providers.build_chat_model(
            self.node.data.model or DEFAULT_EXTRACT_MODEL,
            langsmith_config=get_current_langsmith(),
        )
        messages: list[BaseMessage] = [
            HumanMessage(content=f"Extract structured data from:\n\n{input_text}"),
        ]
        result = await _so.structured_invoke(
            chat_model,
            messages,
            schema=self.node.data.json_schema,
            json_mode=self.node.data.json_schema is None,
        )
        parsed = _coerce_to_dict(result)

        return {
            "variables": {"lastOutput": parsed},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"input": input_text[:200]},
                    "output": parsed,
                }
            },
        }


__all__ = ["DEFAULT_EXTRACT_MODEL", "ExtractExecutor", "ExtractNodeError"]
