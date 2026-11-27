"""JoinExecutor -- the `join` node type.

Explicit convergence point for parallel branches: any number of
incoming edges, exactly one outgoing edge (enforced by
validate_workflow_shape in src/engine/graph_builder.py). Purely
structural: this executor reads nothing from state and does no work beyond
standard node bookkeeping.

WHEN it runs is decided by how `build_graph` wires it (`plan_join`), not
here. Parallel paths do NOT arrive in the same superstep -- paths of
different lengths arrive in different ones -- so a Join fed by parallel paths
is a barrier (`add_edge([lanes...], join)`) that fires once, after every lane
has arrived. A Join whose inputs are alternatives (if/else branches meeting,
a loop re-entering) keeps plain edges and runs once per arrival.
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
