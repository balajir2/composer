"""Start node executor.

Port of OAB's lib/workflow/langgraph.ts:569-588 with the deliberate
clarification documented in the Phase 1 spec §7.1: Composer explicitly
writes `variables.lastOutput` so a direct start→end workflow produces
a useful finalOutput rather than OAB's empty default.
"""

import json
from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import StartNode
from src.executors.base import register_executor


@register_executor("start")
class StartExecutor:
    def __init__(self, node: StartNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_input = state["variables"].get("input", "")

        parsed: Any = raw_input
        if isinstance(raw_input, str):
            try:
                parsed = json.loads(raw_input)
            except (json.JSONDecodeError, ValueError):
                parsed = raw_input

        if isinstance(parsed, dict):
            variables_delta = {**parsed, "lastOutput": parsed}
        else:
            variables_delta = {"input": parsed, "lastOutput": parsed}

        return {
            "variables": variables_delta,
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": raw_input,
                    "output": parsed,
                }
            },
        }


__all__ = ["StartExecutor"]
