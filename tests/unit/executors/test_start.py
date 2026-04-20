"""Tests for the Start node executor."""

import pytest

from src.engine.state import initial_state
from src.engine.workflow import StartNode
from src.executors.start import StartExecutor


@pytest.fixture
def start_node() -> StartNode:
    return StartNode.model_validate(
        {
            "id": "s",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "data": {"label": "Start"},
        }
    )


async def test_start_plain_string_input(start_node: StartNode) -> None:
    state = initial_state("hello world")
    delta = await StartExecutor(start_node).arun(state)
    assert delta["variables"]["input"] == "hello world"
    assert delta["variables"]["lastOutput"] == "hello world"
    assert delta["current_node_id"] == "s"
    assert delta["node_results"]["s"]["status"] == "completed"


async def test_start_json_string_input_parses_and_spreads(start_node: StartNode) -> None:
    state = initial_state('{"user_message": "hi", "turn": 3}')
    delta = await StartExecutor(start_node).arun(state)
    assert delta["variables"]["user_message"] == "hi"
    assert delta["variables"]["turn"] == 3
    assert delta["variables"]["lastOutput"] == {"user_message": "hi", "turn": 3}


async def test_start_dict_input_spreads(start_node: StartNode) -> None:
    state = initial_state({"foo": 1, "bar": [2, 3]})
    delta = await StartExecutor(start_node).arun(state)
    assert delta["variables"]["foo"] == 1
    assert delta["variables"]["bar"] == [2, 3]
    assert delta["variables"]["lastOutput"] == {"foo": 1, "bar": [2, 3]}


async def test_start_non_json_string_stays_string(start_node: StartNode) -> None:
    state = initial_state("not { valid json")
    delta = await StartExecutor(start_node).arun(state)
    assert delta["variables"]["input"] == "not { valid json"
    assert delta["variables"]["lastOutput"] == "not { valid json"


async def test_start_is_registered() -> None:
    """build_executor should resolve 'start' to StartExecutor."""
    from src.executors.base import build_executor

    node = StartNode.model_validate(
        {
            "id": "s",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "data": {"label": "S"},
        }
    )
    executor = build_executor(node)
    assert isinstance(executor, StartExecutor)
