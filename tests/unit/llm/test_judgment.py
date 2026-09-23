"""Tests for src/llm/judgment.py — the JudgmentProvider abstraction and
its LLM-backed implementation."""

from typing import Any

import httpx
import pytest

from src.engine.workflow import DecisionExample, DecisionOption
from src.llm.judgment import (
    JudgmentProviderError,
    LLMJudgmentProvider,
    TypeSafeJudgmentProvider,
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


def test_build_judgment_provider_typesafe_returns_typesafe_provider():
    provider = build_judgment_provider("typesafe")
    assert isinstance(provider, TypeSafeJudgmentProvider)


async def test_typesafe_decide_binary_sends_noul_question(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import judgment as judgment_mod

    captured: dict[str, Any] = {}

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    async def _fake_post(
        self: httpx.AsyncClient, url: str, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"answers": {"decision": {"type": "noul", "noul": 0.9}}},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    result, confidence = await provider.decide_binary(
        instruction="Is this urgent?",
        examples=[DecisionExample(input="fire", result=True)],
        text="server is down",
        model=None,
    )
    assert result is True
    assert confidence == 0.9
    assert captured["url"] == "https://api.typesafe.ai/v1/systemone"
    assert captured["json"]["state"] == "server is down"
    assert captured["json"]["questions"]["decision"]["type"] == "noul"
    assert "fire" in captured["json"]["questions"]["decision"]["instructions"]
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


async def test_typesafe_decide_binary_defaults_model_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: TypeSafe's live API rejects a request with no `model`
    field with a 422 ("Field required"), contradicting the docs' claim that it
    defaults to jev-latest server-side. Found via a real admin "test
    connection" click against the live API."""
    from src.llm import judgment as judgment_mod

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    captured: dict[str, Any] = {}

    async def _fake_post(
        self: httpx.AsyncClient, url: str, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200, json={"answers": {"decision": {"type": "noul", "noul": 0.9}}}, request=request
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    await provider.decide_binary(instruction="x?", examples=[], text="y", model=None)
    assert captured["json"]["model"] == "jev-latest"


async def test_typesafe_decide_binary_low_noul_means_false_with_inverted_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.llm import judgment as judgment_mod

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    async def _fake_post(
        self: httpx.AsyncClient, url: str, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200, json={"answers": {"decision": {"type": "noul", "noul": 0.1}}}, request=request
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    result, confidence = await provider.decide_binary(
        instruction="x?", examples=[], text="y", model=None
    )
    assert result is False
    assert confidence == pytest.approx(0.9)


async def test_typesafe_decide_choice_sends_choice_question_with_criteria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.llm import judgment as judgment_mod

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    captured: dict[str, Any] = {}

    async def _fake_post(
        self: httpx.AsyncClient, url: str, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "answers": {"decision": {"type": "choice", "choice": "billing", "confidence": 0.8}}
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    option, confidence = await provider.decide_choice(
        instruction="Route it.",
        options=[
            DecisionOption(label="billing", description="money stuff"),
            DecisionOption(label="technical"),
        ],
        examples=[],
        text="I was overcharged",
        model=None,
    )
    assert option == "billing"
    assert confidence == 0.8
    q = captured["json"]["questions"]["decision"]
    assert q["type"] == "choice"
    # Regression guard: TypeSafe's live API 422s on a missing `model` field,
    # so it must always default to jev-latest when the caller didn't set one.
    assert captured["json"]["model"] == "jev-latest"
    assert q["criteria"] == {"billing": "money stuff"}


async def test_typesafe_raises_judgment_provider_error_on_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.llm import judgment as judgment_mod

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    async def _fake_post(
        self: httpx.AsyncClient, url: str, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(401, text="unauthorized", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    with pytest.raises(JudgmentProviderError, match="401"):
        await provider.decide_binary(instruction="x?", examples=[], text="y", model=None)


async def test_typesafe_passes_model_field_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import judgment as judgment_mod

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    captured: dict[str, Any] = {}

    async def _fake_post(
        self: httpx.AsyncClient, url: str, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200, json={"answers": {"decision": {"type": "noul", "noul": 0.9}}}, request=request
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    await provider.decide_binary(instruction="x?", examples=[], text="y", model="jev-1.13.0")
    assert captured["json"]["model"] == "jev-1.13.0"
