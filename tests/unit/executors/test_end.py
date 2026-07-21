"""Tests for the End node executor."""

import pytest

from src.engine.state import initial_state
from src.engine.workflow import EndNode
from src.executors.end import EndExecutor


@pytest.fixture
def end_node() -> EndNode:
    return EndNode.model_validate(
        {
            "id": "e",
            "type": "end",
            "position": {"x": 100, "y": 0},
            "data": {"label": "End"},
        }
    )


async def test_end_returns_last_output(end_node: EndNode) -> None:
    state = initial_state()
    state["variables"]["lastOutput"] = {"answer": 42}
    delta = await EndExecutor(end_node).arun(state)
    assert delta["final_outputs"] == {"e": {"answer": 42}}
    assert delta["current_node_id"] == "e"
    assert delta["node_results"]["e"]["status"] == "completed"


async def test_end_tolerates_missing_last_output(end_node: EndNode) -> None:
    state = initial_state()
    # variables has {"input": "", "lastOutput": ""} from initial_state
    state["variables"].pop("lastOutput")
    delta = await EndExecutor(end_node).arun(state)
    assert delta["final_outputs"] == {"e": None}


async def test_end_is_registered() -> None:
    from src.executors.base import build_executor

    node = EndNode.model_validate(
        {
            "id": "e",
            "type": "end",
            "position": {"x": 0, "y": 0},
            "data": {"label": "E"},
        }
    )
    executor = build_executor(node)
    assert isinstance(executor, EndExecutor)
