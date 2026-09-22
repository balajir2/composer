"""decision node executor.

Resolves input text and dispatches to the configured JudgmentProvider
(default "llm"). Returns {decision, confidence} without touching
lastOutput — same transparent-passthrough contract as guardrails.

See docs/superpowers/specs/2026-09-22-decision-node-design.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.executors.base import register_executor
from src.llm.judgment import build_judgment_provider

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import DecisionNode


@register_executor("decision")
class DecisionExecutor:
    def __init__(self, node: DecisionNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        variables = state.get("variables") or {}
        if "lastOutput" in variables:
            input_raw = variables["lastOutput"]
        elif "input" in variables:
            input_raw = variables["input"]
        else:
            input_raw = ""
        text = input_raw if isinstance(input_raw, str) else str(input_raw)

        data = self.node.data
        provider = build_judgment_provider(data.provider or "llm")

        if data.mode == "binary":
            result, confidence = await provider.decide_binary(
                instruction=data.instruction,
                examples=data.examples or [],
                text=text,
                model=data.model,
            )
            decision: bool | str = result
        else:
            assert data.options is not None
            option, confidence = await provider.decide_choice(
                instruction=data.instruction,
                options=data.options,
                examples=data.examples or [],
                text=text,
                model=data.model,
            )
            decision = option

        return {
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"mode": data.mode, "provider": data.provider or "llm"},
                    "output": {"decision": decision, "confidence": confidence},
                }
            },
        }


__all__ = ["DecisionExecutor"]
