"""Tests for the Join node executor."""

import pytest

from src.engine.state import initial_state
from src.engine.workflow import JoinNode
from src.executors.join import JoinExecutor


@pytest.fixture
def join_node() -> JoinNode:
    return JoinNode.model_validate(
        {
            "id": "j",
            "type": "join",
            "position": {"x": 100, "y": 0},
            "data": {"label": "Join"},
        }
    )


async def test_join_returns_minimal_delta(join_node: JoinNode) -> None:
    state = initial_state()
    delta = await JoinExecutor(join_node).arun(state)
    assert delta == {
        "current_node_id": "j",
        "node_results": {"j": {"node_id": "j", "status": "completed"}},
    }


async def test_join_is_registered() -> None:
    from src.executors.base import build_executor

    node = JoinNode.model_validate(
        {"id": "j", "type": "join", "position": {"x": 0, "y": 0}, "data": {"label": "J"}}
    )
    executor = build_executor(node)
    assert isinstance(executor, JoinExecutor)
