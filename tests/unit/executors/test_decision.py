"""Tests for the decision executor."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.engine.state import initial_state
from src.engine.workflow import DecisionNode
from src.executors.decision import DecisionExecutor


def _node(**data_overrides: Any) -> DecisionNode:
    data: dict[str, Any] = {"label": "D", "mode": "binary", "instruction": "Is this urgent?"}
    data.update(data_overrides)
    return DecisionNode.model_validate(
        {"id": "d1", "type": "decision", "position": {"x": 0, "y": 0}, "data": data}
    )


class _FakeProvider:
    def __init__(
        self,
        binary_result: tuple[bool, float] | None = None,
        choice_result: tuple[str, float] | None = None,
    ) -> None:
        self.decide_binary = AsyncMock(return_value=binary_result)
        self.decide_choice = AsyncMock(return_value=choice_result)


async def test_binary_mode_calls_decide_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(binary_result=(True, 0.9))

    def _fake_build_judgment_provider(name: str) -> _FakeProvider:
        return fake

    monkeypatch.setattr(dec_mod, "build_judgment_provider", _fake_build_judgment_provider)

    state = initial_state()
    state["variables"]["lastOutput"] = "server is down"
    delta = await DecisionExecutor(_node()).arun(state)

    fake.decide_binary.assert_awaited_once()
    call_kwargs = fake.decide_binary.call_args.kwargs
    assert call_kwargs["text"] == "server is down"
    assert call_kwargs["instruction"] == "Is this urgent?"

    output = delta["node_results"]["d1"]["output"]
    assert output == {"decision": True, "confidence": 0.9}
    assert "lastOutput" not in delta.get("variables", {})


async def test_choice_mode_calls_decide_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(choice_result=("billing", 0.7))

    def _fake_build_judgment_provider(name: str) -> _FakeProvider:
        return fake

    monkeypatch.setattr(dec_mod, "build_judgment_provider", _fake_build_judgment_provider)

    state = initial_state()
    state["variables"]["lastOutput"] = "I was overcharged"
    node = _node(
        mode="choice",
        instruction="Route this ticket.",
        options=[{"label": "billing"}, {"label": "technical"}],
    )
    delta = await DecisionExecutor(node).arun(state)

    fake.decide_choice.assert_awaited_once()
    output = delta["node_results"]["d1"]["output"]
    assert output == {"decision": "billing", "confidence": 0.7}


async def test_defaults_to_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    captured = {}

    def _capture(name: str):
        captured["name"] = name
        return _FakeProvider(binary_result=(True, 1.0))

    monkeypatch.setattr(dec_mod, "build_judgment_provider", _capture)

    state = initial_state()
    state["variables"]["lastOutput"] = "x"
    await DecisionExecutor(_node()).arun(state)
    assert captured["name"] == "llm"


async def test_last_output_precedence_over_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(binary_result=(True, 1.0))

    def _fake_build_judgment_provider(name: str) -> _FakeProvider:
        return fake

    monkeypatch.setattr(dec_mod, "build_judgment_provider", _fake_build_judgment_provider)

    state = initial_state()
    state["variables"]["input"] = "INPUT_VAL"
    state["variables"]["lastOutput"] = "LAST_OUTPUT_VAL"
    await DecisionExecutor(_node()).arun(state)

    assert fake.decide_binary.call_args.kwargs["text"] == "LAST_OUTPUT_VAL"


async def test_falsy_last_output_not_replaced_by_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(binary_result=(False, 1.0))

    def _fake_build_judgment_provider(name: str) -> _FakeProvider:
        return fake

    monkeypatch.setattr(dec_mod, "build_judgment_provider", _fake_build_judgment_provider)

    state = initial_state()
    state["variables"]["input"] = "INPUT_FALLBACK"
    state["variables"]["lastOutput"] = 0
    await DecisionExecutor(_node()).arun(state)

    assert fake.decide_binary.call_args.kwargs["text"] == "0"


async def test_executor_is_registered() -> None:
    import src.executors.decision  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    executor = build_executor(_node())
    assert isinstance(executor, DecisionExecutor)


async def test_node_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(binary_result=(True, 0.5))

    def _fake_build_judgment_provider(name: str) -> _FakeProvider:
        return fake

    monkeypatch.setattr(dec_mod, "build_judgment_provider", _fake_build_judgment_provider)

    state = initial_state()
    state["variables"]["lastOutput"] = "x"
    delta = await DecisionExecutor(_node()).arun(state)

    result = delta["node_results"]["d1"]
    assert result["status"] == "completed"
    assert result["input"]["mode"] == "binary"
    assert result["input"]["provider"] == "llm"
    assert delta["current_node_id"] == "d1"
