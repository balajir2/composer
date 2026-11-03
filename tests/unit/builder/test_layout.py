from src.builder.layout import compute_layout
from src.engine.workflow import (
    AgentNode,
    AgentNodeData,
    EndNode,
    EndNodeData,
    Position,
    StartNode,
    StartNodeData,
    WorkflowEdge,
    WorkflowNode,
)


def _wf() -> tuple[list[WorkflowNode], list[WorkflowEdge]]:
    nodes: list[WorkflowNode] = [
        StartNode(id="s", type="start", position=Position(x=0, y=0), data=StartNodeData(label="S")),
        AgentNode(id="a", type="agent", position=Position(x=0, y=0), data=AgentNodeData(label="A")),
        EndNode(id="e", type="end", position=Position(x=0, y=0), data=EndNodeData(label="E")),
    ]
    edges: list[WorkflowEdge] = [
        WorkflowEdge(id="e1", source="s", target="a"),
        WorkflowEdge(id="e2", source="a", target="e"),
    ]
    return nodes, edges


def test_layout_places_start_leftmost_and_end_rightmost():
    nodes, edges = _wf()
    pos = compute_layout(nodes, edges)
    assert pos["s"].x < pos["a"].x < pos["e"].x


def test_layout_is_deterministic():
    nodes, edges = _wf()
    assert compute_layout(nodes, edges) == compute_layout(nodes, edges)


def test_layout_covers_all_node_ids():
    nodes, edges = _wf()
    pos = compute_layout(nodes, edges)
    assert set(pos) == {n.id for n in nodes}
