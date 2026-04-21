"""Tests for the set-state executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import SetStateNode
from src.executors.set_state import SetStateExecutor


def _set_state_node(**data: Any) -> SetStateNode:
    return SetStateNode.model_validate(
        {
            "id": "s",
            "type": "set-state",
            "position": {"x": 0, "y": 0},
            "data": {"label": "S", **data},
        }
    )


async def test_set_state_writes_variable() -> None:
    node = _set_state_node(stateKey="name", stateValue="Ada")
    delta = await SetStateExecutor(node).arun(initial_state())
    assert delta["variables"]["name"] == "Ada"
    assert delta["variables"]["lastOutput"] == "Ada"
    assert delta["current_node_id"] == "s"
    assert delta["node_results"]["s"]["status"] == "completed"


async def test_set_state_substitutes_in_value() -> None:
    node = _set_state_node(
        stateKey="greeting",
        stateValue="hello {{name}}",
    )
    state = initial_state()
    state["variables"]["name"] = "Ada"
    delta = await SetStateExecutor(node).arun(state)
    assert delta["variables"]["greeting"] == "hello Ada"


async def test_set_state_supports_nested_value() -> None:
    node = _set_state_node(
        stateKey="config",
        stateValue={"apiKey": "{{key}}", "retries": 3},
    )
    state = initial_state()
    state["variables"]["key"] = "sk-1"
    delta = await SetStateExecutor(node).arun(state)
    assert delta["variables"]["config"] == {"apiKey": "sk-1", "retries": 3}


async def test_set_state_missing_key_raises() -> None:
    node = _set_state_node(stateValue="anything")
    with pytest.raises(ValueError, match="stateKey"):
        await SetStateExecutor(node).arun(initial_state())


async def test_set_state_executor_is_registered() -> None:
    import src.executors.set_state  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _set_state_node(stateKey="x", stateValue=1)
    executor = build_executor(node)
    assert isinstance(executor, SetStateExecutor)
