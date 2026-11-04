"""Tests for deterministic start/end orchestration of builder drafts.

The 2026-11-04 defect: a model asked to build onto the default
Start -> End canvas created a *second* Start node, left the End node
disconnected and the new nodes unreachable, producing a graph that failed
validation. `normalize_orchestration` repairs these mechanical mistakes after
every build turn so the canvas is always runnable end to end.
"""

from src.builder.draft import Draft
from src.builder.normalize import normalize_orchestration
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import (
    AgentNode,
    AgentNodeData,
    EndNode,
    EndNodeData,
    IfElseNode,
    IfElseNodeData,
    Position,
    StartInputVariable,
    StartNode,
    StartNodeData,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

_POS = Position(x=0, y=0)


def _start(node_id: str, *variables: str) -> StartNode:
    data = StartNodeData(
        label="Start", inputVariables=[StartInputVariable(name=v) for v in variables]
    )  # pyright: ignore[reportCallIssue]
    return StartNode(id=node_id, type="start", position=_POS, data=data)


def _end(node_id: str) -> EndNode:
    return EndNode(id=node_id, type="end", position=_POS, data=EndNodeData(label="End"))


def _agent(node_id: str) -> AgentNode:
    return AgentNode(id=node_id, type="agent", position=_POS, data=AgentNodeData(label=node_id))


def _draft(nodes: list[WorkflowNode], edges: list[tuple[str, str]]) -> Draft:
    draft = Draft()
    for node in nodes:
        draft.nodes[node.id] = node
    for i, (source, target) in enumerate(edges):
        draft.edges[f"e{i}"] = WorkflowEdge(id=f"e{i}", source=source, target=target)
    return draft


def _violations(draft: Draft) -> list[str]:
    nodes, edges = draft.snapshot()
    return collect_workflow_violations(Workflow(name="t", nodes=nodes, edges=edges))


def _edge_pairs(draft: Draft) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in draft.edges.values()}


def test_duplicate_start_is_merged_into_the_existing_one() -> None:
    """The screenshot case: default start-1/end-1 plus a model-made `start`."""
    draft = _draft(
        [_start("start-1"), _end("end-1"), _start("start"), _agent("search")],
        [("start-1", "end-1"), ("start", "search")],
    )

    normalize_orchestration(draft)

    assert [n.id for n in draft.nodes.values() if n.type == "start"] == ["start-1"]
    assert ("start-1", "search") in _edge_pairs(draft)
    assert _violations(draft) == []


def test_new_chain_replaces_the_direct_start_to_end_edge() -> None:
    draft = _draft(
        [_start("s"), _end("e"), _agent("a")],
        [("s", "e"), ("s", "a"), ("a", "e")],
    )

    normalize_orchestration(draft)

    assert _edge_pairs(draft) == {("s", "a"), ("a", "e")}
    assert _violations(draft) == []


def test_disconnected_end_is_wired_to_the_last_node() -> None:
    draft = _draft(
        [_start("s"), _agent("a"), _agent("b"), _end("e")],
        [("s", "a"), ("a", "b")],
    )

    normalize_orchestration(draft)

    assert ("b", "e") in _edge_pairs(draft)
    assert _violations(draft) == []


def test_orphan_chain_head_is_connected_from_start() -> None:
    draft = _draft(
        [_start("s"), _agent("a"), _agent("b"), _end("e")],
        [("a", "b"), ("b", "e")],
    )

    normalize_orchestration(draft)

    assert ("s", "a") in _edge_pairs(draft)
    assert _violations(draft) == []


def test_missing_end_node_is_created_and_connected() -> None:
    draft = _draft([_start("s"), _agent("a")], [("s", "a")])

    normalize_orchestration(draft)

    ends = [n for n in draft.nodes.values() if n.type == "end"]
    assert len(ends) == 1
    assert ("a", ends[0].id) in _edge_pairs(draft)
    assert _violations(draft) == []


def test_missing_start_node_is_created_and_connected() -> None:
    draft = _draft([_agent("a"), _end("e")], [("a", "e")])

    normalize_orchestration(draft)

    starts = [n for n in draft.nodes.values() if n.type == "start"]
    assert len(starts) == 1
    assert (starts[0].id, "a") in _edge_pairs(draft)
    assert _violations(draft) == []


def test_extra_start_input_variables_are_carried_over() -> None:
    draft = _draft(
        [_start("s"), _start("dup", "topic"), _agent("a"), _end("e")],
        [("dup", "a"), ("a", "e")],
    )

    normalize_orchestration(draft)

    kept = draft.nodes["s"]
    assert isinstance(kept, StartNode)
    assert [v.name for v in kept.data.input_variables] == ["topic"]


def test_already_valid_graph_is_left_untouched() -> None:
    draft = _draft([_start("s"), _agent("a"), _end("e")], [("s", "a"), ("a", "e")])

    normalize_orchestration(draft)

    assert _edge_pairs(draft) == {("s", "a"), ("a", "e")}
    assert len(draft.nodes) == 3


def test_conditional_nodes_are_not_given_unlabelled_edges() -> None:
    """Branching needs a branch label the model must choose; do not guess."""
    node = IfElseNode(
        id="i", type="if-else", position=_POS, data=IfElseNodeData(label="i", condition="True")
    )
    draft = _draft([_start("s"), node, _end("e")], [("s", "i")])

    normalize_orchestration(draft)

    assert all(e.source != "i" for e in draft.edges.values())
