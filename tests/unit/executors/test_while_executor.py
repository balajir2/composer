"""Tests for the while executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import WhileNode
from src.executors.while_loop import (
    WhileExecutor,
    WhileMaxIterationsError,
    WhileNodeError,
)


def _while_node(**data: Any) -> WhileNode:
    return WhileNode.model_validate(
        {
            "id": "w",
            "type": "while",
            "position": {"x": 0, "y": 0},
            "data": {"label": "W", **data},
        }
    )


async def test_while_condition_true_routes_body() -> None:
    node = _while_node(condition="variables['counter'] > 0")
    state = initial_state()
    state["variables"]["counter"] = 3
    delta = await WhileExecutor(node).arun(state)
    assert delta["node_results"]["w"]["output"]["taken"] == "body"
    assert delta["node_results"]["w"]["output"]["evaluated"] is True


async def test_while_condition_false_routes_exit() -> None:
    node = _while_node(condition="variables['counter'] > 0")
    state = initial_state()
    state["variables"]["counter"] = 0
    delta = await WhileExecutor(node).arun(state)
    assert delta["node_results"]["w"]["output"]["taken"] == "exit"


async def test_while_iteration_counter_bumps() -> None:
    node = _while_node(condition="True")
    state = initial_state()
    # First pass
    delta1 = await WhileExecutor(node).arun(state)
    assert delta1["variables"]["_while_iterations"]["w"] == 1
    # Simulate state after LangGraph merges the delta
    state["variables"]["_while_iterations"] = delta1["variables"]["_while_iterations"]
    delta2 = await WhileExecutor(node).arun(state)
    assert delta2["variables"]["_while_iterations"]["w"] == 2


async def test_while_max_iterations_cap_raises() -> None:
    node = _while_node(condition="True", maxIterations=3)
    state = initial_state()
    # Pre-seed the counter at cap
    state["variables"]["_while_iterations"] = {"w": 3}
    with pytest.raises(WhileMaxIterationsError, match="max_iterations=3"):
        await WhileExecutor(node).arun(state)


async def test_while_missing_condition_raises() -> None:
    node = _while_node()  # no condition
    with pytest.raises(ValueError, match="condition"):
        await WhileExecutor(node).arun(initial_state())


async def test_while_eval_error_wraps() -> None:
    node = _while_node(condition="ghost + 1")
    with pytest.raises(WhileNodeError):
        await WhileExecutor(node).arun(initial_state())


async def test_while_executor_is_registered() -> None:
    import src.executors.while_loop  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _while_node(condition="True")
    executor = build_executor(node)
    assert isinstance(executor, WhileExecutor)
