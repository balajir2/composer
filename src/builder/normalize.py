"""Deterministic start/end orchestration for builder drafts.

Models building onto the default ``Start -> End`` canvas routinely make the same
mechanical mistakes: a second Start node, an End node left disconnected, new
chains that nothing connects to Start, or a leftover direct ``Start -> End``
edge running in parallel with the new chain. These repairs need no judgement,
so they are applied in code after every build turn instead of being left to the
prompt. Anything that does need judgement (branch labels, several sinks that
would all feed one End) is left for the validator to report back to the model.
"""

import secrets

from src.builder.draft import Draft
from src.engine.graph_builder import CONDITIONAL_SOURCE_TYPES
from src.engine.workflow import (
    EndNode,
    EndNodeData,
    Position,
    StartInputVariable,
    StartNode,
    StartNodeData,
    WorkflowEdge,
)

_VISUAL_ONLY = {"note", "file-trigger"}


def _unique_id(draft: Draft, base: str) -> str:
    candidate = base
    while candidate in draft.nodes:
        candidate = f"{base}-{secrets.token_hex(2)}"
    return candidate


def _add_edge(draft: Draft, source: str, target: str) -> None:
    edge_id = f"e-{secrets.token_hex(4)}"
    draft.edges[edge_id] = WorkflowEdge(id=edge_id, source=source, target=target)


def _executable(draft: Draft, node_id: str) -> bool:
    node = draft.nodes.get(node_id)
    return node is not None and node.type not in _VISUAL_ONLY


def _merge_extra_starts(draft: Draft) -> None:
    starts = [n for n in draft.nodes.values() if n.type == "start"]
    if len(starts) < 2:
        return
    kept = starts[0]
    assert isinstance(kept, StartNode)
    variables: list[StartInputVariable] = list(kept.data.input_variables)
    known = {v.name for v in variables}
    for extra in starts[1:]:
        assert isinstance(extra, StartNode)
        for variable in extra.data.input_variables:
            if variable.name not in known:
                variables.append(variable)
                known.add(variable.name)
        for edge_id, edge in list(draft.edges.items()):
            if edge.target == extra.id:
                del draft.edges[edge_id]
            elif edge.source == extra.id:
                draft.edges[edge_id] = edge.model_copy(update={"source": kept.id})
        del draft.nodes[extra.id]
    data = StartNodeData(**{**kept.data.model_dump(by_alias=True), "inputVariables": variables})  # pyright: ignore[reportArgumentType]
    draft.nodes[kept.id] = StartNode(id=kept.id, type="start", position=kept.position, data=data)


def _ensure_endpoints(draft: Draft) -> None:
    if not any(n.type == "start" for n in draft.nodes.values()) and draft.nodes:
        node_id = _unique_id(draft, "start")
        draft.nodes[node_id] = StartNode(
            id=node_id, type="start", position=Position(x=0, y=0), data=StartNodeData(label="Start")
        )
    if not any(n.type == "end" for n in draft.nodes.values()) and draft.nodes:
        node_id = _unique_id(draft, "end")
        draft.nodes[node_id] = EndNode(
            id=node_id, type="end", position=Position(x=0, y=0), data=EndNodeData(label="End")
        )


def normalize_orchestration(draft: Draft) -> None:
    """Repair start/end wiring in place. A no-op for an already-valid graph."""
    _merge_extra_starts(draft)
    _ensure_endpoints(draft)

    start = next((n for n in draft.nodes.values() if n.type == "start"), None)
    end_ids = [n.id for n in draft.nodes.values() if n.type == "end"]
    if start is None:
        return

    def has_executable_incoming(node_id: str) -> bool:
        return any(
            e.target == node_id and _executable(draft, e.source) for e in draft.edges.values()
        )

    def has_executable_outgoing(node_id: str) -> bool:
        return any(
            e.source == node_id and _executable(draft, e.target) for e in draft.edges.values()
        )

    # Chain heads: executable nodes nothing leads into hang off Start.
    for node in list(draft.nodes.values()):
        if node.id == start.id or node.type in _VISUAL_ONLY or node.type == "end":
            continue
        if not has_executable_incoming(node.id):
            _add_edge(draft, start.id, node.id)

    # A leftover Start -> End shortcut must not run beside the real chain.
    for edge_id, edge in list(draft.edges.items()):
        if edge.source != start.id or edge.target not in end_ids:
            continue
        others = [
            e
            for e in draft.edges.values()
            if e is not edge and (e.source == start.id or e.target == edge.target)
        ]
        if others:
            del draft.edges[edge_id]

    # Chain tails feed the End node. Branching nodes need labels the model must
    # choose, and several sinks into one End is a validator-reported problem.
    if len(end_ids) != 1:
        return
    end_id = end_ids[0]
    for node in list(draft.nodes.values()):
        if node.id in (start.id, end_id) or node.type in _VISUAL_ONLY:
            continue
        if node.type in CONDITIONAL_SOURCE_TYPES or node.type == "end":
            continue
        if not has_executable_outgoing(node.id):
            _add_edge(draft, node.id, end_id)
    if not has_executable_incoming(end_id):
        _add_edge(draft, start.id, end_id)
