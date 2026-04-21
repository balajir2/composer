"""Tests for the if-else executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import IfElseNode
from src.executors.if_else import IfElseExecutor, IfElseNodeError


def _if_else_node(**data: Any) -> IfElseNode:
    return IfElseNode.model_validate(
        {
            "id": "ie",
            "type": "if-else",
            "position": {"x": 0, "y": 0},
            "data": {"label": "IE", **data},
        }
    )


async def test_if_else_truthy_condition() -> None:
    node = _if_else_node(condition="variables['x'] > 0")
    state = initial_state()
    state["variables"]["x"] = 5
    delta = await IfElseExecutor(node).arun(state)
    assert delta["current_node_id"] == "ie"
    assert delta["node_results"]["ie"]["status"] == "completed"
    assert delta["node_results"]["ie"]["output"]["taken"] == "true"
    assert delta["node_results"]["ie"]["output"]["evaluated"] is True


async def test_if_else_falsy_condition() -> None:
    node = _if_else_node(condition="variables['x'] > 0")
    state = initial_state()
    state["variables"]["x"] = -1
    delta = await IfElseExecutor(node).arun(state)
    assert delta["node_results"]["ie"]["output"]["taken"] == "false"
    assert delta["node_results"]["ie"]["output"]["evaluated"] is False


async def test_if_else_missing_condition_raises() -> None:
    node = _if_else_node()  # no condition
    with pytest.raises(ValueError, match="condition"):
        await IfElseExecutor(node).arun(initial_state())


async def test_if_else_eval_error_wraps() -> None:
    node = _if_else_node(condition="ghost + 1")
    with pytest.raises(IfElseNodeError):
        await IfElseExecutor(node).arun(initial_state())


async def test_if_else_executor_is_registered() -> None:
    import src.executors.if_else  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _if_else_node(condition="True")
    executor = build_executor(node)
    assert isinstance(executor, IfElseExecutor)
