"""Tests for the guardrails executor."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.state import initial_state
from src.engine.workflow import GuardrailsNode
from src.executors.guardrails import (
    GuardrailsExecutor,
    GuardrailsNodeError,
    GuardrailViolationError,
)


def _node(**data_overrides: Any) -> GuardrailsNode:
    data: dict[str, Any] = {"label": "GR"}
    data.update(data_overrides)
    return GuardrailsNode.model_validate(
        {
            "id": "gr",
            "type": "guardrails",
            "position": {"x": 0, "y": 0},
            "data": data,
        }
    )


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


def _stub_llm(responses: list[str]) -> MagicMock:
    """Stub LLM whose ainvoke returns the next response in `responses`."""
    responses_iter = iter(responses)
    llm = MagicMock()

    async def _ainvoke(messages: Any) -> _FakeResponse:
        return _FakeResponse(next(responses_iter))

    llm.ainvoke = AsyncMock(side_effect=_ainvoke)
    return llm


async def test_no_checks_enabled_returns_passed_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """When nothing is enabled, executor returns passed=True without calling LLM."""
    from src.executors import guardrails as gr_mod

    called = {"n": 0}

    def _fake_build(*a: Any, **kw: Any) -> Any:
        called["n"] += 1
        return MagicMock()

    monkeypatch.setattr(gr_mod, "build_chat_model", _fake_build)

    delta = await GuardrailsExecutor(_node()).arun(initial_state())
    assert delta["variables"]["_guardrails_result"]["passed"] is True
    assert delta["variables"]["_guardrails_result"]["checks_run"] == []
    assert delta["variables"]["_guardrails_result"]["message"] == "no checks configured"
    assert called["n"] == 0


async def test_single_check_no_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: _stub_llm(["NO"]))  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello world"
    delta = await GuardrailsExecutor(_node(piiEnabled=True)).arun(state)
    assert delta["variables"]["_guardrails_result"]["passed"] is True
    assert delta["variables"]["_guardrails_result"]["checks_run"] == ["pii"]
    assert delta["variables"]["_guardrails_result"]["violations"] == []


async def test_single_check_violation_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: _stub_llm(["YES"]))  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello world"
    delta = await GuardrailsExecutor(_node(piiEnabled=True, actionOnViolation="warn")).arun(state)
    result = delta["variables"]["_guardrails_result"]
    assert result["passed"] is False
    assert result["violations"] == ["PII detected"]
    assert "PII" in delta["variables"]["lastOutput"]


async def test_violation_block_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: _stub_llm(["YES"]))  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello world"
    with pytest.raises(GuardrailViolationError):
        await GuardrailsExecutor(_node(piiEnabled=True, actionOnViolation="block")).arun(state)


async def test_multiple_checks_some_violate(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    # Order is (pii, moderation, jailbreak, hallucination) per _enabled_checks()
    monkeypatch.setattr(
        gr_mod,
        "build_chat_model",
        lambda *a, **kw: _stub_llm(["NO", "YES", "NO"]),  # pyright: ignore[reportUnknownLambdaType]
    )

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello world"
    node = _node(piiEnabled=True, moderationEnabled=True, jailbreakEnabled=True)
    delta = await GuardrailsExecutor(node).arun(state)
    result = delta["variables"]["_guardrails_result"]
    assert result["passed"] is False
    assert result["checks_run"] == ["pii", "moderation", "jailbreak"]
    assert result["violations"] == ["harmful content detected"]


async def test_ambiguous_llm_response_treated_as_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the LLM returns an ambiguous non-YES response, treat as NO (no false positives)."""
    from src.executors import guardrails as gr_mod

    monkeypatch.setattr(
        gr_mod,
        "build_chat_model",
        lambda *a, **kw: _stub_llm(["Well, it depends..."]),  # pyright: ignore[reportUnknownLambdaType]
    )

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello"
    delta = await GuardrailsExecutor(_node(piiEnabled=True)).arun(state)
    assert delta["variables"]["_guardrails_result"]["passed"] is True


async def test_empty_llm_response_treated_as_no(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: _stub_llm([""]))  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello"
    delta = await GuardrailsExecutor(_node(piiEnabled=True)).arun(state)
    assert delta["variables"]["_guardrails_result"]["passed"] is True


async def test_llm_raises_wrapped_as_guardrails_node_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.executors import guardrails as gr_mod

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("llm boom"))
    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: llm)  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello"
    with pytest.raises(GuardrailsNodeError, match="pii"):
        await GuardrailsExecutor(_node(piiEnabled=True)).arun(state)


async def test_last_output_precedence_over_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    captured_prompts: list[str] = []

    class _CapturingLLM:
        async def ainvoke(self, messages: list[Any]) -> _FakeResponse:
            human = messages[1]
            captured_prompts.append(human.content)
            return _FakeResponse("NO")

    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: _CapturingLLM())  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["input"] = "INPUT_VAL"
    state["variables"]["lastOutput"] = "LAST_OUTPUT_VAL"
    await GuardrailsExecutor(_node(piiEnabled=True)).arun(state)

    assert "LAST_OUTPUT_VAL" in captured_prompts[0]
    assert "INPUT_VAL" not in captured_prompts[0]


async def test_non_string_input_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    captured_prompts: list[str] = []

    class _CapturingLLM:
        async def ainvoke(self, messages: list[Any]) -> _FakeResponse:
            captured_prompts.append(messages[1].content)
            return _FakeResponse("NO")

    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: _CapturingLLM())  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["lastOutput"] = {"nested": "dict"}
    await GuardrailsExecutor(_node(piiEnabled=True)).arun(state)

    assert "nested" in captured_prompts[0]


async def test_executor_is_registered() -> None:
    import src.executors.guardrails  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from src.executors.base import build_executor

    node = _node(piiEnabled=True)
    executor = build_executor(node)
    assert isinstance(executor, GuardrailsExecutor)


async def test_node_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import guardrails as gr_mod

    monkeypatch.setattr(gr_mod, "build_chat_model", lambda *a, **kw: _stub_llm(["NO"]))  # pyright: ignore[reportUnknownLambdaType]

    state = initial_state()
    state["variables"]["lastOutput"] = "Hello"
    delta = await GuardrailsExecutor(_node(piiEnabled=True, actionOnViolation="warn")).arun(state)
    result = delta["node_results"]["gr"]
    assert result["status"] == "completed"
    assert result["input"]["checks_run"] == ["pii"]
    assert result["input"]["action_on_violation"] == "warn"
    assert result["output"]["passed"] is True
