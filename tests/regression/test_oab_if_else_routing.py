"""Regression — if-else behavioural contract (OAB parity).

OAB reference: lib/workflow/executors/if-else.ts
  - Condition truthy → true branch target node runs next
  - Condition falsy  → false branch target node runs next
  - Missing condition → execution fails at node build time

Tests the executor + router in isolation via direct invocation.
No Neon required for this regression; it's pure logic.
"""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import IfElseNode
from src.executors.if_else import IfElseExecutor, IfElseNodeError

pytestmark = pytest.mark.integration


def _if_else_node(**data: Any) -> IfElseNode:
    return IfElseNode.model_validate(
        {
            "id": "ie",
            "type": "if-else",
            "position": {"x": 0, "y": 0},
            "data": {"label": "IE", **data},
        }
    )


async def test_oab_regression_truthy_condition_records_true_branch() -> None:
    node = _if_else_node(condition="variables['answer'] == 42")
    state = initial_state()
    state["variables"]["answer"] = 42
    delta = await IfElseExecutor(node).arun(state)
    assert delta["node_results"]["ie"]["output"]["taken"] == "true"


async def test_oab_regression_falsy_condition_records_false_branch() -> None:
    node = _if_else_node(condition="variables['answer'] == 42")
    state = initial_state()
    state["variables"]["answer"] = 41
    delta = await IfElseExecutor(node).arun(state)
    assert delta["node_results"]["ie"]["output"]["taken"] == "false"


async def test_oab_regression_missing_condition_fails_at_execution() -> None:
    node = _if_else_node()  # no condition
    with pytest.raises(ValueError, match="condition"):
        await IfElseExecutor(node).arun(initial_state())


async def test_oab_regression_undefined_variable_wraps() -> None:
    node = _if_else_node(condition="variables['ghost'] > 0")
    state = initial_state()
    with pytest.raises(IfElseNodeError):
        await IfElseExecutor(node).arun(state)
