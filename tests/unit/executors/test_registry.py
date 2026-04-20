"""Tests for the executor registry."""

from typing import Any

import pytest

from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode, StartNode
from src.executors.base import build_executor, register_executor


def test_register_and_lookup() -> None:
    @register_executor("_test_fake")
    class FakeExec:
        def __init__(self, node: Any) -> None:
            self.node = node

        async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
            return {}

    node = AgentNode.model_validate(
        {
            "id": "n",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "a"},
        }
    )
    # Swap the type tag via model_copy since registration is by string, not class
    fake_node = node.model_copy(update={"type": "_test_fake"})  # type: ignore[arg-type]
    executor = build_executor(fake_node)
    assert isinstance(executor, FakeExec)


def test_unshipped_type_raises_with_phase_hint() -> None:
    from src.engine.workflow import HttpNode

    node = HttpNode.model_validate(
        {
            "id": "n",
            "type": "http",
            "position": {"x": 0, "y": 0},
            "data": {"label": "h"},
        }
    )
    with pytest.raises(NotImplementedError) as excinfo:
        build_executor(node)
    assert "'http'" in str(excinfo.value)
    assert "Phase 4" in str(excinfo.value)


def test_unknown_type_raises_generic_message() -> None:
    # Fabricate a node with an unmapped type by constructing StartNode then
    # tampering with its .type attribute in a model_copy.
    node = StartNode.model_validate(
        {
            "id": "n",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "data": {"label": "s"},
        }
    )
    unmapped = node.model_copy(update={"type": "_never_heard_of"})  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError) as excinfo:
        build_executor(unmapped)
    assert "_never_heard_of" in str(excinfo.value)
    assert "later phase" in str(excinfo.value)
