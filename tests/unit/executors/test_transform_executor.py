"""Tests for the transform executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import TransformNode
from src.executors.transform import TransformExecutor, TransformNodeError


def _transform_node(**data: Any) -> TransformNode:
    return TransformNode.model_validate(
        {
            "id": "t",
            "type": "transform",
            "position": {"x": 0, "y": 0},
            "data": {"label": "T", **data},
        }
    )


async def test_transform_arithmetic() -> None:
    node = _transform_node(transformScript="variables['x'] * 2")
    state = initial_state()
    state["variables"]["x"] = 21
    delta = await TransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == 42


async def test_transform_last_output_shorthand() -> None:
    node = _transform_node(transformScript="lastOutput.upper()")
    state = initial_state()
    state["variables"]["lastOutput"] = "hello"
    delta = await TransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "HELLO"


async def test_transform_missing_script_raises() -> None:
    node = _transform_node()  # no transformScript
    with pytest.raises(ValueError, match="transformScript"):
        await TransformExecutor(node).arun(initial_state())


async def test_transform_syntax_error_raises() -> None:
    node = _transform_node(transformScript="1 +* 2")
    with pytest.raises(TransformNodeError):
        await TransformExecutor(node).arun(initial_state())


async def test_transform_dunder_access_blocked() -> None:
    node = _transform_node(transformScript="variables['x'].__class__")
    state = initial_state()
    state["variables"]["x"] = "hello"
    with pytest.raises(TransformNodeError):
        await TransformExecutor(node).arun(state)


async def test_transform_executor_is_registered() -> None:
    import src.executors.transform  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _transform_node(transformScript="1")
    executor = build_executor(node)
    assert isinstance(executor, TransformExecutor)
