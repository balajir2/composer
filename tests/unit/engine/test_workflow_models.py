"""Tests for Workflow / WorkflowEdge / WorkflowNode Pydantic models."""

import pytest
from pydantic import ValidationError

from src.engine.workflow import (
    EndNode,
    Position,
    StartNode,
    Workflow,
    WorkflowEdge,
)


def test_position_accepts_numeric_coords() -> None:
    p = Position(x=10.5, y=-3)
    assert p.x == 10.5
    assert p.y == -3.0


def test_start_node_parses_minimal() -> None:
    n = StartNode.model_validate(
        {
            "id": "n1",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "data": {"label": "Start"},
        }
    )
    assert n.id == "n1"
    assert n.type == "start"
    assert n.data.label == "Start"
    assert n.data.input_variables == []


def test_start_node_accepts_input_variables_camelcase() -> None:
    n = StartNode.model_validate(
        {
            "id": "n1",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "Start",
                "inputVariables": [
                    {
                        "name": "user_message",
                        "type": "string",
                        "required": True,
                        "description": "The user's input",
                    }
                ],
            },
        }
    )
    assert len(n.data.input_variables) == 1
    assert n.data.input_variables[0].name == "user_message"
    assert n.data.input_variables[0].required is True


def test_end_node_parses_minimal() -> None:
    n = EndNode.model_validate(
        {
            "id": "n2",
            "type": "end",
            "position": {"x": 200, "y": 0},
            "data": {"label": "End"},
        }
    )
    assert n.type == "end"


def test_edge_accepts_alias_sourceHandle() -> None:
    e = WorkflowEdge.model_validate(
        {"id": "e1", "source": "n1", "target": "n2", "sourceHandle": "if"}
    )
    assert e.source_handle == "if"


def test_workflow_parses_start_to_end() -> None:
    wf = Workflow.model_validate(
        {
            "name": "Smoke",
            "nodes": [
                {
                    "id": "n1",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "Start"},
                },
                {
                    "id": "n2",
                    "type": "end",
                    "position": {"x": 200, "y": 0},
                    "data": {"label": "End"},
                },
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
        }
    )
    assert wf.name == "Smoke"
    assert len(wf.nodes) == 2
    assert wf.nodes[0].type == "start"
    assert wf.nodes[1].type == "end"


def test_workflow_rejects_unknown_node_type() -> None:
    with pytest.raises(ValidationError):
        Workflow.model_validate(
            {
                "name": "Bad",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "nope",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "?"},
                    },
                ],
                "edges": [],
            }
        )


def test_workflow_round_trip_preserves_camelcase_aliases() -> None:
    """parse(camelCase JSON) -> dump(by_alias=True) -> re-parse preserves all fields.

    Guards against silent alias breakage when Task 4 adds 16 more node types -
    any missed `alias=` on a new field shows up here first.
    """
    original = {
        "name": "RoundTrip",
        "userId": "user-42",
        "estimatedTime": "5m",
        "isTemplate": True,
        "isPublic": False,
        "createdAt": "2026-04-20T00:00:00Z",
        "nodes": [
            {
                "id": "n1",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Start",
                    "nodeType": "custom",
                    "nodeName": "MyStart",
                    "inputVariables": [
                        {
                            "name": "x",
                            "type": "string",
                            "required": True,
                            "description": "first arg",
                            "defaultValue": "hello",
                        }
                    ],
                },
            },
            {
                "id": "n2",
                "type": "end",
                "position": {"x": 200, "y": 0},
                "data": {"label": "End"},
            },
        ],
        "edges": [{"id": "e1", "source": "n1", "target": "n2", "sourceHandle": "out"}],
    }
    parsed = Workflow.model_validate(original)
    dumped = parsed.model_dump(by_alias=True, exclude_none=True)
    reparsed = Workflow.model_validate(dumped)

    assert reparsed.name == "RoundTrip"
    assert reparsed.user_id == "user-42"
    assert reparsed.estimated_time == "5m"
    assert reparsed.is_template is True
    assert reparsed.edges[0].source_handle == "out"

    # Narrow the discriminated-union node to StartNode so pyright sees
    # StartNodeData and the inputVariables field.
    start_node = reparsed.nodes[0]
    assert isinstance(start_node, StartNode)
    assert start_node.data.node_name == "MyStart"
    assert start_node.data.input_variables[0].default_value == "hello"


ALL_NODE_TYPES = [
    "start",
    "end",
    "note",
    "agent",
    "mcp",
    "if-else",
    "while",
    "user-approval",
    "transform",
    "data-transform",
    "set-state",
    "extract",
    "http",
    "guardrails",
    "vector-db",
    "gamma-ai",
    "arcade",
    "join-chunks",
]


@pytest.mark.parametrize("node_type", ALL_NODE_TYPES)
def test_every_node_type_parses_minimal_instance(node_type: str) -> None:
    wf = Workflow.model_validate(
        {
            "name": "T",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "x",
                    "type": node_type,
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "X"},
                },
                {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "x"},
                {"id": "e2", "source": "x", "target": "e"},
            ],
        }
    )
    # start, x, end — verify the middle node parsed with the requested type
    assert wf.nodes[1].type == node_type


def test_all_18_types_exhaustive() -> None:
    assert len(ALL_NODE_TYPES) == 18


def test_workflow_edge_branch_defaults_none() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate({"id": "e1", "source": "a", "target": "b"})
    assert edge.branch is None


def test_workflow_edge_branch_accepts_string() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate({"id": "e1", "source": "x", "target": "y", "branch": "true"})
    assert edge.branch == "true"


def test_workflow_edge_branch_round_trip_json() -> None:
    from src.engine.workflow import WorkflowEdge

    edge = WorkflowEdge.model_validate({"id": "e1", "source": "x", "target": "y", "branch": "body"})
    dumped = edge.model_dump(mode="json")
    assert dumped["branch"] == "body"
