"""Workflow → LangGraph StateGraph compilation.

Task 8 implements validation. Task 9 adds compile().

Reference: OAB lib/workflow/langgraph.ts:169-427.
"""

from collections import deque
from collections.abc import Callable, Hashable, Iterable
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.engine.events_wrapper import wrap_executor_with_events
from src.engine.state import WorkflowStateDict
from src.engine.workflow import (
    IfElseNode,
    UserApprovalNode,
    WhileNode,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

# Executors are registered as a side effect of import; importing them here
# ensures the registry is populated before build_graph reads it.
from src.executors import (
    agent as _agent_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    arcade as _arcade_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    confluence as _confluence_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    data_transform as _data_transform_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    download_pdf as _download_pdf_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    email as _email_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import end as _end_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
from src.executors import (
    extract as _extract_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    file_write as _file_write_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    gamma_ai as _gamma_ai_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    guardrails as _guardrails_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    http as _http_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    if_else as _if_else_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    jira as _jira_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    join_chunks as _join_chunks_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
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
from src.executors import (
    user_approval as _user_approval_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    vector_db as _vector_db_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors import (
    while_loop as _while_loop_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from src.executors._eval import EvalError, evaluate
from src.executors.base import build_executor


class WorkflowValidationError(ValueError):
    """Raised when a workflow's shape is invalid before execution."""


# Node types that never execute: they are pure canvas annotations / metadata
# consumed by tooling outside the execution graph (note = author's memo;
# file-trigger = config read by the `composer watch` CLI). graph_builder
# skips them entirely and workflow-shape validation allows them disconnected.
_VISUAL_ONLY_TYPES = {"note", "file-trigger"}


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
    """BFS from start; every non-visual-only node must be reachable.

    Uses the same edge projection build_graph uses at compile time (edges
    touching a visual-only node on either end are dropped — see the
    "normal edges" pass below) so a node reachable only via a detour
    through a note/file-trigger isn't falsely validated as reachable
    when it would actually be disconnected once compiled (P0-8).
    """
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        if nodes[edge.source].type in _VISUAL_ONLY_TYPES:
            continue
        if nodes[edge.target].type in _VISUAL_ONLY_TYPES:
            continue
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
        if node.type in _VISUAL_ONLY_TYPES:
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

    # Phase 4b/5a: branch labels must match source type.
    _conditional_types = {"if-else", "while", "user-approval"}
    for edge in workflow.edges:
        source = nodes[edge.source]
        if source.type in _conditional_types:
            if edge.branch is None:
                raise WorkflowValidationError(
                    f"Edge {edge.id!r} leaves {source.type} node {source.id!r} "
                    f"but has no branch label."
                )
        else:
            if edge.branch is not None:
                raise WorkflowValidationError(
                    f"Edge {edge.id!r} has branch={edge.branch!r} but its source "
                    f"{source.id!r} is not a conditional node."
                )

    _check_reachability(start_ids[0], nodes, workflow.edges)


CONDITIONAL_SOURCE_TYPES = {"if-else", "while", "user-approval"}


def _branch_mapping(
    node: WorkflowNode,
    edges: list[WorkflowEdge],
    required_branches: set[str],
) -> dict[str, str]:
    """Build a {branch: target_node_id} mapping for a conditional source.

    Raises WorkflowValidationError if any edge has no branch label, duplicate
    branch labels exist, or the edge set doesn't exactly match
    `required_branches`.
    """
    out_edges = [e for e in edges if e.source == node.id]
    seen: dict[str, str] = {}
    for e in out_edges:
        if e.branch is None:
            raise WorkflowValidationError(
                f"Edge {e.id!r} leaving {node.type} node {node.id!r} has no branch label."
            )
        if e.branch in seen:
            raise WorkflowValidationError(
                f"Duplicate branch {e.branch!r} on edges leaving {node.id!r}."
            )
        if e.branch not in required_branches:
            raise WorkflowValidationError(
                f"Edge {e.id!r} leaving {node.type} node {node.id!r} has "
                f"unexpected branch {e.branch!r}; allowed: {sorted(required_branches)}."
            )
        seen[e.branch] = e.target
    missing = required_branches - seen.keys()
    if missing:
        raise WorkflowValidationError(
            f"{node.type} node {node.id!r} branches mismatch: "
            f"missing={sorted(missing)}, got={sorted(seen.keys())}."
        )
    return seen


def _route_if_else(node: IfElseNode) -> Callable[[WorkflowStateDict], str]:
    """Router closure for an if-else node.  Evaluates the condition fresh on
    each traversal (not reading from node_results — that can be stale on retry).
    Returns the fallback 'false' on EvalError to avoid wedging the graph.
    """
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "false"
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "false"
        return "true" if result else "false"

    return _router


def _route_while(node: WhileNode) -> Callable[[WorkflowStateDict], str]:
    """Router closure for a while node.  Same logic as _route_if_else but
    returns 'body' / 'exit' branch keys.  The executor handles the
    max-iterations check before this router runs.
    """
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "exit"
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "exit"
        return "body" if result else "exit"

    return _router


def _route_user_approval(
    node: UserApprovalNode,
) -> Callable[[WorkflowStateDict], str]:
    """Router for user-approval: reads _approval_<node_id> from variables."""

    def _router(state: WorkflowStateDict) -> str:
        variables = state.get("variables") or {}
        decision = variables.get(f"_approval_{node.id}")
        return "approved" if decision == "approved" else "rejected"

    return _router


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
        if node.type in _VISUAL_ONLY_TYPES:
            continue  # visual-only; skipped at build time per OAB behavior
        executor = build_executor(node)  # may raise NotImplementedError
        arun_with_events = wrap_executor_with_events(executor, node)
        builder.add_node(node.id, arun_with_events)  # pyright: ignore[reportUnknownMemberType,reportArgumentType]

    # Emit normal edges first; conditional edges handled in a second pass.
    for edge in workflow.edges:
        source_node = nodes_by_id[edge.source]
        if source_node.type in {"if-else", "while", "user-approval"}:
            continue  # handled by conditional-edges pass below
        # Skip edges whose source or target is a visual-only node
        if source_node.type in _VISUAL_ONLY_TYPES:
            continue
        if nodes_by_id[edge.target].type in _VISUAL_ONLY_TYPES:
            continue
        builder.add_edge(edge.source, edge.target)  # pyright: ignore[reportUnknownMemberType]

    # Conditional edges pass — AFTER normal edges + all nodes are in place.
    for node in workflow.nodes:
        if node.type == "if-else":
            assert isinstance(node, IfElseNode)
            mapping = cast(
                "dict[Hashable, str]",
                _branch_mapping(node, list(workflow.edges), {"true", "false"}),
            )
            builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                node.id, _route_if_else(node), mapping
            )
        elif node.type == "while":
            assert isinstance(node, WhileNode)
            mapping = cast(
                "dict[Hashable, str]",
                _branch_mapping(node, list(workflow.edges), {"body", "exit"}),
            )
            builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                node.id, _route_while(node), mapping
            )
        elif node.type == "user-approval":
            assert isinstance(node, UserApprovalNode)
            mapping = cast(
                "dict[Hashable, str]",
                _branch_mapping(node, list(workflow.edges), {"approved", "rejected"}),
            )
            builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                node.id, _route_user_approval(node), mapping
            )

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
