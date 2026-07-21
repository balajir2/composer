"""End node executor.

Port of OAB's lib/workflow/langgraph.ts:653-654. Reads
state.variables.lastOutput and surfaces it keyed by this End node's own
id in final_outputs -- not a shared "finalOutput" key -- so two End
nodes firing in the same LangGraph superstep (parallel fan-out, each
branch terminating at its own End) never race: merge_dict's shallow
union accumulates disjoint per-node-id keys correctly regardless of
concurrent execution order, the same pattern node_results already
relies on. src/engine/langgraph_executor.py's _mark_completed collapses
a single-entry final_outputs dict back to a plain scalar for the
common (single-End) case.
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
            "final_outputs": {self.node.id: last_output},
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
