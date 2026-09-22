"""Tests for src/llm/judgment.py — the JudgmentProvider abstraction and
its LLM-backed implementation."""

from typing import Any

import pytest

from src.engine.workflow import DecisionExample, DecisionOption
from src.llm.judgment import (
    JudgmentProviderError,
    LLMJudgmentProvider,
    build_judgment_provider,
)


def test_build_judgment_provider_llm_returns_llm_provider():
    provider = build_judgment_provider("llm")
    assert isinstance(provider, LLMJudgmentProvider)


def test_build_judgment_provider_unknown_raises():
    with pytest.raises(JudgmentProviderError, match="unknown judgment provider"):
        build_judgment_provider("nonexistent")


class _FakeStructured:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def ainvoke(self, messages: Any) -> Any:
        return self._result


class _FakeChatModel:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.captured_schema: Any = None
        self.captured_messages: Any = None

    def with_structured_output(self, schema: Any) -> _FakeStructured:
        self.captured_schema = schema
        return _FakeStructured(self._result)


async def test_decide_binary_returns_result_and_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import judgment as judgment_mod

    class _Result:
        result = True
        confidence = 0.92

    fake_model = _FakeChatModel(_Result())
    monkeypatch.setattr(
        judgment_mod,
        "build_chat_model",
        lambda *a, **kw: fake_model,  # pyright: ignore[reportUnknownLambdaType]
    )

    provider = LLMJudgmentProvider()
    result, confidence = await provider.decide_binary(
        instruction="Is this urgent?", examples=[], text="server is down", model=None
    )
    assert result is True
    assert confidence == 0.92


async def test_decide_binary_folds_examples_into_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import judgment as judgment_mod

    class _Result:
        result = False
        confidence = 0.5

    fake_model = _FakeChatModel(_Result())
    captured: dict[str, Any] = {}

    def _capture_build(*a: Any, **kw: Any) -> Any:
        return fake_model

    monkeypatch.setattr(judgment_mod, "build_chat_model", _capture_build)

    real_structured_invoke = judgment_mod.structured_invoke

    async def _capturing_invoke(chat_model: Any, messages: list[Any], **kw: Any) -> Any:
        captured["messages"] = messages
        return await real_structured_invoke(chat_model, messages, **kw)

    monkeypatch.setattr(judgment_mod, "structured_invoke", _capturing_invoke)

    provider = LLMJudgmentProvider()
    await provider.decide_binary(
        instruction="Is this urgent?",
        examples=[DecisionExample(input="fire in the building", result=True)],
        text="nice weather today",
        model=None,
    )
    prompt_text = captured["messages"][-1].content
    assert "fire in the building" in prompt_text
    assert "Examples:" in prompt_text
    assert "nice weather today" in prompt_text


async def test_decide_choice_builds_literal_schema_from_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.llm import judgment as judgment_mod

    class _Result:
        option = "billing"
        confidence = 0.8

    fake_model = _FakeChatModel(_Result())
    monkeypatch.setattr(
        judgment_mod,
        "build_chat_model",
        lambda *a, **kw: fake_model,  # pyright: ignore[reportUnknownLambdaType]
    )

    provider = LLMJudgmentProvider()
    option, confidence = await provider.decide_choice(
        instruction="Route this ticket.",
        options=[DecisionOption(label="billing"), DecisionOption(label="technical")],
        examples=[],
        text="I was overcharged",
        model=None,
    )
    assert option == "billing"
    assert confidence == 0.8
    # Schema's `option` field must be constrained to the passed-in labels.
    schema = fake_model.captured_schema
    field_info = schema.model_fields["option"]
    assert set(field_info.annotation.__args__) == {"billing", "technical"}


async def test_decide_binary_uses_default_model_when_none_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.executors.guardrails import DEFAULT_MODEL
    from src.llm import judgment as judgment_mod

    class _Result:
        result = True
        confidence = 1.0

    captured: dict[str, Any] = {}

    def _capture_build(model_string: str, **kw: Any) -> Any:
        captured["model_string"] = model_string
        captured["temperature"] = kw.get("temperature")
        return _FakeChatModel(_Result())

    monkeypatch.setattr(judgment_mod, "build_chat_model", _capture_build)

    provider = LLMJudgmentProvider()
    await provider.decide_binary(instruction="x?", examples=[], text="y", model=None)
    assert captured["model_string"] == DEFAULT_MODEL
    assert captured["temperature"] == 0.0
