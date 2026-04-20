"""End node executor.

Port of OAB's lib/workflow/langgraph.ts:653-654. Reads
state.variables.lastOutput and surfaces it as finalOutput for the
orchestrator to persist on the WorkflowExecution row.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import EndNode
from src.executors.base import register_executor


@register_executor("end")
class EndExecutor:
    def __init__(self, node: EndNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        last_output = state["variables"].get("lastOutput")
        return {
            "variables": {"finalOutput": last_output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "output": last_output,
                }
            },
        }


__all__ = ["EndExecutor"]
