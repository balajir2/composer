"""set-state node executor.

Writes a (optionally templated) value into state.variables[stateKey].
Also aliases the value as `variables.lastOutput` so downstream nodes can
consume it without naming the key explicitly.

See Phase 4a spec §7.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import SetStateNode
from src.executors.base import register_executor
from src.variable_substitution import substitute_in_value


@register_executor("set-state")
class SetStateExecutor:
    def __init__(self, node: SetStateNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        state_key = self.node.data.state_key
        if not state_key:
            raise ValueError(f"set-state node {self.node.id!r} requires stateKey")
        resolved = substitute_in_value(self.node.data.state_value, state)
        return {
            "variables": {
                state_key: resolved,
                "lastOutput": resolved,
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {state_key: resolved},
                    "output": resolved,
                }
            },
        }


__all__ = ["SetStateExecutor"]
