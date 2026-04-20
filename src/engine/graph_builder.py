"""Workflow → LangGraph StateGraph compilation.

Task 8 implements validation. Task 9 adds compile().

Reference: OAB lib/workflow/langgraph.ts:169-427.
"""

from collections import deque
from collections.abc import Iterable

from src.engine.workflow import Workflow, WorkflowEdge, WorkflowNode


class WorkflowValidationError(ValueError):
    """Raised when a workflow's shape is invalid before execution."""


def _nodes_by_id(nodes: Iterable[WorkflowNode]) -> dict[str, WorkflowNode]:
    seen: dict[str, WorkflowNode] = {}
    for node in nodes:
        if node.id in seen:
            raise WorkflowValidationError(f"Node id {node.id!r} is duplicated.")
        seen[node.id] = node
    return seen


def _check_edges(edges: Iterable[WorkflowEdge], ids: set[str]) -> None:
    for edge in edges:
        if edge.source not in ids:
            raise WorkflowValidationError(
                f"Edge {edge.id!r} references unknown source node id {edge.source!r}."
            )
        if edge.target not in ids:
            raise WorkflowValidationError(
                f"Edge {edge.id!r} references unknown target node id {edge.target!r}."
            )


def _check_reachability(
    start_id: str, nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]
) -> None:
    """BFS from start; every non-note node must be reachable."""
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        outgoing[edge.source].append(edge.target)

    reachable: set[str] = set()
    queue: deque[str] = deque([start_id])
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(outgoing[current])

    for node_id, node in nodes.items():
        if node.type == "note":
            continue  # visual-only, allowed disconnected
        if node_id not in reachable:
            raise WorkflowValidationError(
                f"Node {node_id!r} ({node.type!r}) is unreachable from the start node."
            )


def validate_workflow_shape(workflow: Workflow) -> None:
    """Validate a workflow's structural invariants. Raises WorkflowValidationError on failure."""
    nodes = _nodes_by_id(workflow.nodes)

    start_ids = [n.id for n in workflow.nodes if n.type == "start"]
    if len(start_ids) != 1:
        raise WorkflowValidationError(
            f"Workflow must contain exactly one start node (found: {len(start_ids)})."
        )

    end_ids = [n.id for n in workflow.nodes if n.type == "end"]
    if not end_ids:
        raise WorkflowValidationError("Workflow must contain at least one end node.")

    _check_edges(workflow.edges, set(nodes.keys()))
    _check_reachability(start_ids[0], nodes, workflow.edges)


__all__ = ["WorkflowValidationError", "validate_workflow_shape"]
