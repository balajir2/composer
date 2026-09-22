"""Tests for the Decision node's Pydantic models (src/engine/workflow.py)."""

import pytest
from pydantic import TypeAdapter, ValidationError

from src.engine.workflow import DecisionNode, WorkflowNode


def _binary_node(**data_overrides):
    data = {"label": "D", "mode": "binary", "instruction": "Is this urgent?"}
    data.update(data_overrides)
    return DecisionNode.model_validate(
        {"id": "d1", "type": "decision", "position": {"x": 0, "y": 0}, "data": data}
    )


def _choice_node(**data_overrides):
    data = {
        "label": "D",
        "mode": "choice",
        "instruction": "Route this ticket.",
        "options": [{"label": "billing"}, {"label": "technical"}],
    }
    data.update(data_overrides)
    return DecisionNode.model_validate(
        {"id": "d1", "type": "decision", "position": {"x": 0, "y": 0}, "data": data}
    )


def test_binary_node_minimal_valid():
    node = _binary_node()
    assert node.data.mode == "binary"
    assert node.data.options is None
    assert node.data.provider is None
    assert node.data.examples is None


def test_choice_node_valid():
    node = _choice_node()
    assert [o.label for o in node.data.options] == ["billing", "technical"]


def test_choice_node_requires_at_least_two_options():
    with pytest.raises(ValidationError, match="at least 2 options"):
        _choice_node(options=[{"label": "only-one"}])


def test_choice_node_requires_non_empty_options():
    with pytest.raises(ValidationError, match="at least 2 options"):
        _choice_node(options=None)


def test_choice_node_rejects_duplicate_labels():
    with pytest.raises(ValidationError, match="unique labels"):
        _choice_node(options=[{"label": "a"}, {"label": "a"}])


def test_examples_round_trip():
    node = _binary_node(
        examples=[
            {"input": "server is down", "result": True},
            {"input": "nice weather", "result": False},
        ]
    )
    assert node.data.examples[0].input == "server is down"
    assert node.data.examples[0].result is True


def test_decision_node_is_in_workflow_node_union():
    adapter = TypeAdapter(WorkflowNode)
    node = adapter.validate_python(
        {
            "id": "d1",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {"label": "D", "mode": "binary", "instruction": "x?"},
        }
    )
    assert isinstance(node, DecisionNode)
