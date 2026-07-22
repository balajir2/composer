"""Tests for the data-transform executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import DataTransformNode
from src.executors.data_transform import (
    SUPPORTED_OPS,
    DataTransformExecutor,
    DataTransformNodeError,
    run_map_filter_reduce,
)


def _dt_node(**data: Any) -> DataTransformNode:
    return DataTransformNode.model_validate(
        {
            "id": "d",
            "type": "data-transform",
            "position": {"x": 0, "y": 0},
            "data": {"label": "D", **data},
        }
    )


async def test_map_doubles_items() -> None:
    node = _dt_node(
        operation="map",
        collection="variables['nums']",
        expression="item * 2",
    )
    state = initial_state()
    state["variables"]["nums"] = [1, 2, 3]
    delta = await DataTransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == [2, 4, 6]


async def test_filter_keeps_positives() -> None:
    node = _dt_node(
        operation="filter",
        collection="variables['nums']",
        expression="item > 0",
    )
    state = initial_state()
    state["variables"]["nums"] = [-1, 0, 1, 2]
    delta = await DataTransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == [1, 2]


async def test_reduce_sums_with_initial() -> None:
    node = _dt_node(
        operation="reduce",
        collection="variables['nums']",
        expression="acc + item",
        initial=0,
    )
    state = initial_state()
    state["variables"]["nums"] = [1, 2, 3, 4]
    delta = await DataTransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == 10


async def test_item_var_override() -> None:
    node = _dt_node(
        operation="map",
        collection="variables['xs']",
        expression="x + 1",
        itemVar="x",
    )
    state = initial_state()
    state["variables"]["xs"] = [10, 20]
    delta = await DataTransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == [11, 21]


async def test_empty_collection_returns_empty_for_map() -> None:
    node = _dt_node(
        operation="map",
        collection="variables['xs']",
        expression="item * 2",
    )
    state = initial_state()
    state["variables"]["xs"] = []
    delta = await DataTransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == []


async def test_non_iterable_collection_raises() -> None:
    node = _dt_node(
        operation="map",
        collection="variables['scalar']",
        expression="item * 2",
    )
    state = initial_state()
    state["variables"]["scalar"] = 42
    with pytest.raises(DataTransformNodeError, match="non-iterable"):
        await DataTransformExecutor(node).arun(state)


async def test_unknown_operation_raises() -> None:
    node = _dt_node(
        operation="sum",  # not supported
        collection="variables['xs']",
        expression="item",
    )
    state = initial_state()
    state["variables"]["xs"] = [1, 2]
    with pytest.raises(DataTransformNodeError, match="sum"):
        await DataTransformExecutor(node).arun(state)


async def test_data_transform_executor_is_registered() -> None:
    import src.executors.data_transform  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _dt_node(
        operation="map",
        collection="[]",
        expression="item",
    )
    executor = build_executor(node)
    assert isinstance(executor, DataTransformExecutor)


def test_supported_ops_is_public() -> None:
    assert {"map", "filter", "reduce"} == SUPPORTED_OPS


def test_run_map_filter_reduce_map() -> None:
    state = initial_state()
    result = run_map_filter_reduce("map", [1, 2, 3], "item * 2", "item", None, state)
    assert result == [2, 4, 6]


def test_run_map_filter_reduce_filter() -> None:
    state = initial_state()
    result = run_map_filter_reduce("filter", [-1, 0, 1, 2], "item > 0", "item", None, state)
    assert result == [1, 2]


def test_run_map_filter_reduce_reduce() -> None:
    state = initial_state()
    result = run_map_filter_reduce("reduce", [1, 2, 3, 4], "acc + item", "item", 0, state)
    assert result == 10
