from src.engine.graph_builder import (
    WorkflowValidationError,
    collect_workflow_violations,
    validate_workflow_shape,
)
from src.engine.workflow import (
    AgentNode,
    AgentNodeData,
    EndNode,
    EndNodeData,
    Position,
    StartNode,
    StartNodeData,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)


def _wf(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> Workflow:
    return Workflow(name="t", nodes=nodes, edges=edges)


def _start(i: str = "s") -> StartNode:
    return StartNode(
        id=i, type="start", position=Position(x=0, y=0), data=StartNodeData(label="Start")
    )


def _end(i: str = "e") -> EndNode:
    return EndNode(
        id=i, type="end", position=Position(x=400, y=0), data=EndNodeData(label="End")
    )


def test_valid_workflow_has_no_violations() -> None:
    wf = _wf([_start(), _end()], [WorkflowEdge(id="e1", source="s", target="e")])
    assert collect_workflow_violations(wf) == []


def test_collects_multiple_violations_in_one_pass() -> None:
    agent = AgentNode(
        id="a", type="agent", position=Position(x=0, y=0), data=AgentNodeData(label="A")
    )
    wf = _wf([_start(), agent], [WorkflowEdge(id="e1", source="s", target="missing")])
    v = collect_workflow_violations(wf)
    assert any("end node" in x for x in v)
    assert any("unknown target node id 'missing'" in x for x in v)
    assert any("unreachable" in x for x in v)


def test_validate_still_raises_on_first() -> None:
    agent = AgentNode(
        id="a", type="agent", position=Position(x=0, y=0), data=AgentNodeData(label="A")
    )
    wf = _wf([_start(), agent], [])
    try:
        validate_workflow_shape(wf)
    except WorkflowValidationError as exc:
        assert "end node" in str(exc)
    else:
        raise AssertionError("expected WorkflowValidationError")
