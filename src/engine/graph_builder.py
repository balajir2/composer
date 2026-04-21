"""Workflow → LangGraph StateGraph compilation.

Task 8 implements validation. Task 9 adds compile().

Reference: OAB lib/workflow/langgraph.ts:169-427.
"""

from collections import deque
from collections.abc import Iterable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.engine.state import WorkflowStateDict
from src.engine.workflow import Workflow, WorkflowEdge, WorkflowNode

# Executors are registered as a side effect of import; importing them here
# ensures the registry is populated before build_graph reads it.
from src.executors import (
    agent as _agent_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    data_transform as _data_transform_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import end as _end_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
from src.executors import (
    http as _http_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    mcp as _mcp_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    set_state as _set_state_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    start as _start_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    transform as _transform_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors.base import build_executor


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


CONDITIONAL_SOURCE_TYPES = {"if-else", "while", "user-approval"}


def build_graph(
    workflow: Workflow,
    checkpointer: BaseCheckpointSaver[Any],
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Validate the workflow, compile it into a LangGraph StateGraph, return the compiled graph.

    LangGraph's generic type parameters (StateT, InputT, OutputT, RawInputT) are
    parameterized with Any here — the state type `WorkflowStateDict` is passed
    to the StateGraph constructor at runtime and LangGraph infers reducers from
    it, but pyright can't bridge the runtime-inferred type to the compile-time
    generics. Any is the honest annotation rather than fighting the type system.
    """
    validate_workflow_shape(workflow)

    builder: StateGraph[Any, Any, Any, Any] = StateGraph(WorkflowStateDict)
    nodes_by_id = {node.id: node for node in workflow.nodes}

    for node in workflow.nodes:
        if node.type == "note":
            continue  # visual-only; skipped at build time per OAB behavior
        executor = build_executor(node)  # may raise NotImplementedError
        builder.add_node(node.id, executor.arun)  # pyright: ignore[reportUnknownMemberType]

    for edge in workflow.edges:
        source_node = nodes_by_id[edge.source]
        if source_node.type in CONDITIONAL_SOURCE_TYPES:
            phase = 4 if source_node.type in {"if-else", "while"} else 5
            raise NotImplementedError(
                f"Conditional edges from node type {source_node.type!r} land in Phase {phase}."
            )
        # Skip edges whose source is a note node (note is not in the graph)
        if source_node.type == "note":
            continue
        # Skip edges whose target is a note node (same reason)
        if nodes_by_id[edge.target].type == "note":
            continue
        builder.add_edge(edge.source, edge.target)

    start_id = next(n.id for n in workflow.nodes if n.type == "start")
    end_ids = [n.id for n in workflow.nodes if n.type == "end"]

    builder.add_edge(START, start_id)
    for end_id in end_ids:
        builder.add_edge(end_id, END)

    return builder.compile(checkpointer=checkpointer)  # pyright: ignore[reportUnknownMemberType]


__all__ = [
    "WorkflowValidationError",
    "build_graph",
    "validate_workflow_shape",
]
