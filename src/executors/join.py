"""JoinExecutor -- the `join` node type.

Explicit convergence point for parallel branches: any number of
incoming edges, exactly one outgoing edge (enforced by
validate_workflow_shape in src/engine/graph_builder.py). Purely
structural -- concurrent branches' state already merges correctly via
merge_dict once LangGraph reaches this node in the same superstep, the
same proven node_results/variables merge behavior parallel branches
already rely on today -- so this executor does no work beyond standard
node bookkeeping.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import JoinNode
from src.executors.base import register_executor


@register_executor("join")
class JoinExecutor:
    def __init__(self, node: JoinNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        return {
            "current_node_id": self.node.id,
            "node_results": {self.node.id: {"node_id": self.node.id, "status": "completed"}},
        }


__all__ = ["JoinExecutor"]
