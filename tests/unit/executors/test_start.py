"""Tests for the Start node executor."""

import pytest

from src.engine.state import initial_state
from src.engine.workflow import StartNode
from src.executors.start import StartExecutor, StartInputValidationError


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


async def test_start_rejects_underscore_prefixed_reserved_key(start_node: StartNode) -> None:
    """P0-7: engine-owned keys like _pending_approval_node / _arcade_retries_*
    must not be injectable via execution input — they're internal
    bookkeeping the executor/checkpointer own."""
    state = initial_state({"topic": "hi", "_pending_approval_node": "approval-1"})
    with pytest.raises(StartInputValidationError, match="_pending_approval_node"):
        await StartExecutor(start_node).arun(state)


async def test_start_rejects_final_output_key(start_node: StartNode) -> None:
    """P0-7: finalOutput is set by nodes/End, never by the caller."""
    state = initial_state({"topic": "hi", "finalOutput": "spoofed"})
    with pytest.raises(StartInputValidationError, match="finalOutput"):
        await StartExecutor(start_node).arun(state)


async def test_start_strict_mode_rejects_undeclared_variable(
    start_node: StartNode, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import get_settings

    monkeypatch.setenv("STRICT_EXECUTION_INPUT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        declared_node = StartNode.model_validate(
            {
                "id": "s",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Start",
                    "inputVariables": [{"name": "topic", "type": "string", "required": True}],
                },
            }
        )
        state = initial_state({"topic": "hi", "unexpected_extra": "sneaky"})
        with pytest.raises(StartInputValidationError, match="unexpected_extra"):
            await StartExecutor(declared_node).arun(state)
    finally:
        monkeypatch.delenv("STRICT_EXECUTION_INPUT_ENABLED", raising=False)
        get_settings.cache_clear()


async def test_start_strict_mode_allows_declared_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import get_settings

    monkeypatch.setenv("STRICT_EXECUTION_INPUT_ENABLED", "true")
    get_settings.cache_clear()
    try:
        declared_node = StartNode.model_validate(
            {
                "id": "s",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Start",
                    "inputVariables": [{"name": "topic", "type": "string", "required": True}],
                },
            }
        )
        state = initial_state({"topic": "hi"})
        delta = await StartExecutor(declared_node).arun(state)
        assert delta["variables"]["topic"] == "hi"
    finally:
        monkeypatch.delenv("STRICT_EXECUTION_INPUT_ENABLED", raising=False)
        get_settings.cache_clear()


async def test_non_strict_mode_allows_undeclared_variable_by_default(
    start_node: StartNode,
) -> None:
    """Backward compatible: strict mode defaults off, so workflows that
    rely on undeclared caller-supplied variables keep working."""
    state = initial_state({"anything": "goes"})
    delta = await StartExecutor(start_node).arun(state)
    assert delta["variables"]["anything"] == "goes"


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
