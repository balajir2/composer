# Decision Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Decision" palette node that branches on an LLM (or TypeSafe/Jev) judgment call instead of a deterministic formula, with TypeSafe reachable only from this node and given full admin-catalog parity with the four existing LLM providers.

**Architecture:** A `JudgmentProvider` interface (`src/llm/judgment.py`) with two implementations — `LLMJudgmentProvider` (wraps Composer's existing `build_chat_model`/`structured_invoke`) and `TypeSafeJudgmentProvider` (raw `httpx` against TypeSafe's `/v1/systemone`). `DecisionExecutor` resolves input text, dispatches to the configured provider, and returns `{decision, confidence}` without touching `lastOutput`. Routing reuses the existing `add_conditional_edges` / `_branch_mapping` machinery already powering if-else/while/user-approval, extended to accept a dynamic (per-node) branch-key set for choice mode. TypeSafe gets full admin key/model-catalog parity but is never reachable from any other node's `model` field.

**Tech Stack:** FastAPI, Pydantic v2, LangGraph Python, LangChain (`build_chat_model`), `httpx` (already a dependency), Next.js/React/vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-09-22-decision-node-design.md` — read it alongside this plan; this plan implements it section-by-section but doesn't restate every rationale.

## Global Constraints

- No DB migration anywhere in this feature (`Workflow.nodes`/`edges` are `Json` columns; `LlmApiKey.provider`/`LlmModel.provider` are free strings).
- No new dependency (`httpx` and `pytest-httpx` are already in `pyproject.toml`).
- TypeSafe must **never** be reachable from `build_chat_model` (`src/llm/providers.py`) or from any node panel's `PROVIDER_OPTIONS` other than `decision.tsx`. The two admin frontend pages are the sole, deliberate exception.
- Every backend/frontend touch to an existing file is additive (new dict/set entry, new `elif` branch, new class) — no existing provider's entry, no existing node type's behavior, changes.
- `temperature=0.0` for `LLMJudgmentProvider`; reuse `guardrails.py`'s `DEFAULT_MODEL` constant, don't duplicate it.
- Follow TDD: write the failing test, watch it fail, implement, watch it pass, commit — for every task with a test step.

---

### Task 1: Backend data model — `DecisionOption`, `DecisionExample`, `DecisionNodeData`, `DecisionNode`

**Files:**
- Modify: `src/engine/workflow.py` (insert near `IfElseNode`, ~line 243; register in the `WorkflowNode` union, ~line 727-754)
- Test: `tests/unit/engine/test_workflow_decision.py`

**Interfaces:**
- Produces: `DecisionOption(label: str, description: str | None)`, `DecisionExample(input: str, result: bool | None, option: str | None)`, `DecisionNodeData(mode: Literal["binary","choice"], instruction: str, examples: list[DecisionExample] | None, options: list[DecisionOption] | None, true_label: str | None, false_label: str | None, model: str | None, provider: str | None)`, `DecisionNode(id: str, type: Literal["decision"], position: Position, data: DecisionNodeData)`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the Decision node's Pydantic models (src/engine/workflow.py)."""

import pytest
from pydantic import ValidationError

from src.engine.workflow import DecisionNode, WorkflowNode
from pydantic import TypeAdapter


def _binary_node(**data_overrides):
    data = {"label": "D", "mode": "binary", "instruction": "Is this urgent?"}
    data.update(data_overrides)
    return DecisionNode.model_validate(
        {"id": "d1", "type": "decision", "position": {"x": 0, "y": 0}, "data": data}
    )


def _choice_node(**data_overrides):
    data = {
        "label": "D",
        "mode": "choice",
        "instruction": "Route this ticket.",
        "options": [{"label": "billing"}, {"label": "technical"}],
    }
    data.update(data_overrides)
    return DecisionNode.model_validate(
        {"id": "d1", "type": "decision", "position": {"x": 0, "y": 0}, "data": data}
    )


def test_binary_node_minimal_valid():
    node = _binary_node()
    assert node.data.mode == "binary"
    assert node.data.options is None
    assert node.data.provider is None
    assert node.data.examples is None


def test_choice_node_valid():
    node = _choice_node()
    assert [o.label for o in node.data.options] == ["billing", "technical"]


def test_choice_node_requires_at_least_two_options():
    with pytest.raises(ValidationError, match="at least 2 options"):
        _choice_node(options=[{"label": "only-one"}])


def test_choice_node_requires_non_empty_options():
    with pytest.raises(ValidationError, match="at least 2 options"):
        _choice_node(options=None)


def test_choice_node_rejects_duplicate_labels():
    with pytest.raises(ValidationError, match="unique labels"):
        _choice_node(options=[{"label": "a"}, {"label": "a"}])


def test_examples_round_trip():
    node = _binary_node(
        examples=[{"input": "server is down", "result": True}, {"input": "nice weather", "result": False}]
    )
    assert node.data.examples[0].input == "server is down"
    assert node.data.examples[0].result is True


def test_decision_node_is_in_workflow_node_union():
    adapter = TypeAdapter(WorkflowNode)
    node = adapter.validate_python(
        {
            "id": "d1",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {"label": "D", "mode": "binary", "instruction": "x?"},
        }
    )
    assert isinstance(node, DecisionNode)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/engine/test_workflow_decision.py -v`
Expected: FAIL with `ImportError: cannot import name 'DecisionNode'`

- [ ] **Step 3: Implement the models**

Insert into `src/engine/workflow.py` immediately after `class IfElseNode` (after line 247, before the `while` section comment):

```python
class DecisionOption(BaseModel):
    label: str
    description: str | None = None


class DecisionExample(BaseModel):
    input: str
    result: bool | None = None  # binary mode
    option: str | None = None  # choice mode


class DecisionNodeData(BaseNodeData):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mode: Literal["binary", "choice"]
    instruction: str
    examples: list[DecisionExample] | None = None
    options: list[DecisionOption] | None = None
    true_label: str | None = Field(default=None, alias="trueLabel")
    false_label: str | None = Field(default=None, alias="falseLabel")
    model: str | None = None
    provider: str | None = None

    @model_validator(mode="after")
    def _validate_choice_options(self) -> "DecisionNodeData":
        if self.mode == "choice":
            if not self.options or len(self.options) < 2:
                raise ValueError("decision node in 'choice' mode requires at least 2 options")
            labels = [o.label for o in self.options]
            if len(labels) != len(set(labels)):
                raise ValueError("decision node options must have unique labels")
        return self


class DecisionNode(BaseModel):
    id: str
    type: Literal["decision"]
    position: Position
    data: DecisionNodeData
```

Then add `| DecisionNode` to the `WorkflowNode` union (insert after `| IfElseNode` at line 735):

```python
    | IfElseNode
    | DecisionNode
    | WhileNode
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/engine/test_workflow_decision.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/engine/workflow.py tests/unit/engine/test_workflow_decision.py
git commit -m "feat(engine): add DecisionNode data model"
```

---

### Task 2: Isolation regression test — TypeSafe must never enter `build_chat_model`

**Files:**
- Test: `tests/unit/llm/test_providers_isolation.py`

**Interfaces:**
- Consumes: `src.llm.providers.build_chat_model` (existing).

This test is written *before* `TypeSafeJudgmentProvider` exists, on purpose — it locks the boundary before there's anything to violate it.

- [ ] **Step 1: Write the failing test**

```python
"""Regression guard: TypeSafe must never be wired into the shared LLM
dispatcher. If this test starts failing because someone added a
"typesafe" branch to _build_raw_chat_model, that's the bug, not this
test — see docs/superpowers/specs/2026-09-22-decision-node-design.md,
'TypeSafe is reachable from the Decision node only'."""

import pytest

from src.llm.providers import build_chat_model


def test_typesafe_prefix_rejected_by_build_chat_model():
    with pytest.raises(Exception):
        build_chat_model("typesafe/jev-latest")
```

- [ ] **Step 2: Run test to verify it fails or passes for the right reason**

Run: `.venv/Scripts/python -m pytest tests/unit/llm/test_providers_isolation.py -v`

This should already PASS today (no `"typesafe"` branch exists yet), which is the point — confirm it passes now, and it stays as a permanent regression guard for every future change to `providers.py`. If it somehow fails (i.e. `build_chat_model` doesn't raise), stop and investigate `_build_raw_chat_model`'s fallback behavior before proceeding with any other task — that would mean the isolation boundary is already broken independent of this feature.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/llm/test_providers_isolation.py
git commit -m "test(llm): lock TypeSafe out of build_chat_model's provider dispatch"
```

---

### Task 3: `JudgmentProvider` abstraction + `LLMJudgmentProvider`

**Files:**
- Create: `src/llm/judgment.py`
- Test: `tests/unit/llm/test_judgment.py`

**Interfaces:**
- Consumes: `src.llm.providers.build_chat_model`, `src.llm.structured_output.structured_invoke`, `src.engine.context.get_current_langsmith`, `src.executors.guardrails.DEFAULT_MODEL`, `src.engine.workflow.DecisionOption`, `src.engine.workflow.DecisionExample`.
- Produces: `JudgmentProviderError(RuntimeError)`, `JudgmentProvider(Protocol)` with `async def decide_binary(self, *, instruction, examples, text, model) -> tuple[bool, float]` and `async def decide_choice(self, *, instruction, options, examples, text, model) -> tuple[str, float]`; `register_judgment_provider(name: str)` decorator; `build_judgment_provider(name: str) -> JudgmentProvider`; `LLMJudgmentProvider` registered as `"llm"`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for src/llm/judgment.py — the JudgmentProvider abstraction and
its LLM-backed implementation."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    monkeypatch.setattr(judgment_mod, "build_chat_model", lambda *a, **kw: fake_model)

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

    async def _capturing_invoke(chat_model, messages, **kw):
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


async def test_decide_choice_builds_literal_schema_from_options(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import judgment as judgment_mod

    class _Result:
        option = "billing"
        confidence = 0.8

    fake_model = _FakeChatModel(_Result())
    monkeypatch.setattr(judgment_mod, "build_chat_model", lambda *a, **kw: fake_model)

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


async def test_decide_binary_uses_default_model_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import judgment as judgment_mod
    from src.executors.guardrails import DEFAULT_MODEL

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/llm/test_judgment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.llm.judgment'`

- [ ] **Step 3: Implement `src/llm/judgment.py`**

```python
"""JudgmentProvider abstraction backing the Decision node executor.

Mirrors src/executors/base.py's Executor/register_executor/build_executor
pattern one layer down: a *provider* a node's executor calls, not the
node's executor itself. LLMJudgmentProvider is the only implementation a
Decision node needs to work end to end; TypeSafeJudgmentProvider (Task 9)
is a second, isolated implementation — see
docs/superpowers/specs/2026-09-22-decision-node-design.md.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, create_model

from src.engine.context import get_current_langsmith
from src.engine.workflow import DecisionExample, DecisionOption
from src.executors.guardrails import DEFAULT_MODEL
from src.llm.providers import build_chat_model
from src.llm.structured_output import structured_invoke

_SYSTEM_PROMPT = "You are a decision-making assistant. Follow the schema exactly."


class JudgmentProviderError(RuntimeError):
    """Raised by a JudgmentProvider on backend failure (network, bad response, etc.)."""


@runtime_checkable
class JudgmentProvider(Protocol):
    async def decide_binary(
        self, *, instruction: str, examples: list[DecisionExample], text: str, model: str | None
    ) -> tuple[bool, float]: ...

    async def decide_choice(
        self,
        *,
        instruction: str,
        options: list[DecisionOption],
        examples: list[DecisionExample],
        text: str,
        model: str | None,
    ) -> tuple[str, float]: ...


_REGISTRY: dict[str, type[Any]] = {}


def register_judgment_provider(name: str):
    def _wrap(cls: type[Any]) -> type[Any]:
        _REGISTRY[name] = cls
        return cls

    return _wrap


def build_judgment_provider(name: str) -> JudgmentProvider:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise JudgmentProviderError(f"unknown judgment provider {name!r}")
    return cls()


def _format_prompt(
    instruction: str, examples: list[DecisionExample], text: str, *, value_key: str
) -> str:
    lines = [instruction]
    if examples:
        lines.append("")
        lines.append("Examples:")
        for i, ex in enumerate(examples, start=1):
            value = ex.result if value_key == "result" else ex.option
            lines.append(f'{i}. Text: "{ex.input}" -> {value}')
    lines.append("")
    lines.append("Now decide:")
    lines.append(f"Text: {text}")
    return "\n".join(lines)


class _BinaryDecision(BaseModel):
    result: bool
    confidence: float


@register_judgment_provider("llm")
class LLMJudgmentProvider:
    async def decide_binary(
        self, *, instruction: str, examples: list[DecisionExample], text: str, model: str | None
    ) -> tuple[bool, float]:
        llm = build_chat_model(
            model or DEFAULT_MODEL,
            temperature=0.0,
            langsmith_config=get_current_langsmith(),
        )
        prompt = _format_prompt(instruction, examples, text, value_key="result")
        try:
            parsed = await structured_invoke(
                llm,
                [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)],
                schema=_BinaryDecision,
            )
        except Exception as exc:
            raise JudgmentProviderError(f"LLM binary decision failed: {exc}") from exc
        return parsed.result, parsed.confidence

    async def decide_choice(
        self,
        *,
        instruction: str,
        options: list[DecisionOption],
        examples: list[DecisionExample],
        text: str,
        model: str | None,
    ) -> tuple[str, float]:
        labels = tuple(opt.label for opt in options)
        from typing import Literal

        schema = create_model(
            "ChoiceDecision",
            option=(Literal[labels], ...),
            confidence=(float, ...),
        )
        llm = build_chat_model(
            model or DEFAULT_MODEL,
            temperature=0.0,
            langsmith_config=get_current_langsmith(),
        )
        prompt = _format_prompt(instruction, examples, text, value_key="option")
        try:
            parsed = await structured_invoke(
                llm,
                [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)],
                schema=schema,
            )
        except Exception as exc:
            raise JudgmentProviderError(f"LLM choice decision failed: {exc}") from exc
        return parsed.option, parsed.confidence


__all__ = [
    "JudgmentProvider",
    "JudgmentProviderError",
    "LLMJudgmentProvider",
    "build_judgment_provider",
    "register_judgment_provider",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/llm/test_judgment.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/llm/judgment.py tests/unit/llm/test_judgment.py
git commit -m "feat(llm): add JudgmentProvider abstraction and LLMJudgmentProvider"
```

---

### Task 4: `DecisionExecutor`

**Files:**
- Create: `src/executors/decision.py`
- Test: `tests/unit/executors/test_decision.py`

**Interfaces:**
- Consumes: `src.llm.judgment.build_judgment_provider`, `src.executors.base.register_executor`, `src.engine.workflow.DecisionNode`.
- Produces: `DecisionExecutor(node: DecisionNode)` with `async def arun(self, state) -> dict`, registered under `"decision"`.

- [ ] **Step 1: Write the failing tests**

```python
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
    def __init__(self, binary_result=None, choice_result=None) -> None:
        self.decide_binary = AsyncMock(return_value=binary_result)
        self.decide_choice = AsyncMock(return_value=choice_result)


async def test_binary_mode_calls_decide_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(binary_result=(True, 0.9))
    monkeypatch.setattr(dec_mod, "build_judgment_provider", lambda name: fake)

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
    monkeypatch.setattr(dec_mod, "build_judgment_provider", lambda name: fake)

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
    monkeypatch.setattr(dec_mod, "build_judgment_provider", lambda name: fake)

    state = initial_state()
    state["variables"]["input"] = "INPUT_VAL"
    state["variables"]["lastOutput"] = "LAST_OUTPUT_VAL"
    await DecisionExecutor(_node()).arun(state)

    assert fake.decide_binary.call_args.kwargs["text"] == "LAST_OUTPUT_VAL"


async def test_falsy_last_output_not_replaced_by_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(binary_result=(False, 1.0))
    monkeypatch.setattr(dec_mod, "build_judgment_provider", lambda name: fake)

    state = initial_state()
    state["variables"]["input"] = "INPUT_FALLBACK"
    state["variables"]["lastOutput"] = 0
    await DecisionExecutor(_node()).arun(state)

    assert fake.decide_binary.call_args.kwargs["text"] == "0"


async def test_executor_is_registered() -> None:
    import src.executors.decision  # noqa: F401
    from src.executors.base import build_executor

    executor = build_executor(_node())
    assert isinstance(executor, DecisionExecutor)


async def test_node_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.executors import decision as dec_mod

    fake = _FakeProvider(binary_result=(True, 0.5))
    monkeypatch.setattr(dec_mod, "build_judgment_provider", lambda name: fake)

    state = initial_state()
    state["variables"]["lastOutput"] = "x"
    delta = await DecisionExecutor(_node()).arun(state)

    result = delta["node_results"]["d1"]
    assert result["status"] == "completed"
    assert result["input"]["mode"] == "binary"
    assert result["input"]["provider"] == "llm"
    assert delta["current_node_id"] == "d1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/executors/test_decision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.executors.decision'`

- [ ] **Step 3: Implement `src/executors/decision.py`**

```python
"""decision node executor.

Resolves input text and dispatches to the configured JudgmentProvider
(default "llm"). Returns {decision, confidence} without touching
lastOutput — same transparent-passthrough contract as guardrails.

See docs/superpowers/specs/2026-09-22-decision-node-design.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.executors.base import register_executor
from src.llm.judgment import build_judgment_provider

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import DecisionNode


@register_executor("decision")
class DecisionExecutor:
    def __init__(self, node: DecisionNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        variables = state.get("variables") or {}
        if "lastOutput" in variables:
            input_raw = variables["lastOutput"]
        elif "input" in variables:
            input_raw = variables["input"]
        else:
            input_raw = ""
        text = input_raw if isinstance(input_raw, str) else str(input_raw)

        data = self.node.data
        provider = build_judgment_provider(data.provider or "llm")

        if data.mode == "binary":
            result, confidence = await provider.decide_binary(
                instruction=data.instruction,
                examples=data.examples or [],
                text=text,
                model=data.model,
            )
            decision: bool | str = result
        else:
            assert data.options is not None
            option, confidence = await provider.decide_choice(
                instruction=data.instruction,
                options=data.options,
                examples=data.examples or [],
                text=text,
                model=data.model,
            )
            decision = option

        return {
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"mode": data.mode, "provider": data.provider or "llm"},
                    "output": {"decision": decision, "confidence": confidence},
                }
            },
        }


__all__ = ["DecisionExecutor"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/executors/test_decision.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/executors/decision.py tests/unit/executors/test_decision.py
git commit -m "feat(executors): add DecisionExecutor"
```

---

### Task 5: `graph_builder.py` wiring — routing, `CONDITIONAL_SOURCE_TYPES`, dispatch

**Files:**
- Modify: `src/engine/graph_builder.py` (imports ~line 19-97, `_conditional_types` ~line 291, `CONDITIONAL_SOURCE_TYPES` ~line 310, normal-edge skip ~line 428, dispatch loop ~line 438-465)
- Test: `tests/unit/engine/test_graph_builder.py` (append)

**Interfaces:**
- Consumes: `src.engine.workflow.DecisionNode`, `src.executors.decision` (import for registration side effect).
- Produces: `_route_decision(node: DecisionNode) -> Callable[[WorkflowStateDict], str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/engine/test_graph_builder.py`:

```python
def test_conditional_edges_compile_for_decision_binary() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "decision binary test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "d",
                    "type": "decision",
                    "position": {"x": 100, "y": 0},
                    "data": {"label": "D", "mode": "binary", "instruction": "urgent?"},
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "d"},
                {"id": "e2", "source": "d", "target": "a", "branch": "true"},
                {"id": "e3", "source": "d", "target": "b", "branch": "false"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_conditional_edges_compile_for_decision_choice_three_way() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from src.engine.graph_builder import build_graph
    from src.engine.workflow import Workflow

    wf = Workflow.model_validate(
        {
            "id": "w1",
            "name": "decision choice test",
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {
                    "id": "d",
                    "type": "decision",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "label": "D",
                        "mode": "choice",
                        "instruction": "route it",
                        "options": [
                            {"label": "billing"},
                            {"label": "technical"},
                            {"label": "sales"},
                        ],
                    },
                },
                {"id": "a", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "A"}},
                {"id": "b", "type": "end", "position": {"x": 200, "y": 100}, "data": {"label": "B"}},
                {"id": "c", "type": "end", "position": {"x": 200, "y": 200}, "data": {"label": "C"}},
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "d"},
                {"id": "e2", "source": "d", "target": "a", "branch": "billing"},
                {"id": "e3", "source": "d", "target": "b", "branch": "technical"},
                {"id": "e4", "source": "d", "target": "c", "branch": "sales"},
            ],
        }
    )
    compiled = build_graph(wf, MemorySaver())
    assert compiled is not None


def test_decision_router_falls_back_on_missing_result() -> None:
    from src.engine.graph_builder import _route_decision
    from src.engine.state import initial_state
    from src.engine.workflow import DecisionNode

    node = DecisionNode.model_validate(
        {
            "id": "d",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {"label": "D", "mode": "binary", "instruction": "x?"},
        }
    )
    router = _route_decision(node)
    state = initial_state()  # no node_results for "d" yet
    assert router(state) == "false"


def test_decision_router_reads_binary_result_from_state() -> None:
    from src.engine.graph_builder import _route_decision
    from src.engine.state import initial_state
    from src.engine.workflow import DecisionNode

    node = DecisionNode.model_validate(
        {
            "id": "d",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {"label": "D", "mode": "binary", "instruction": "x?"},
        }
    )
    router = _route_decision(node)
    state = initial_state()
    state["node_results"]["d"] = {"output": {"decision": True, "confidence": 0.9}}
    assert router(state) == "true"


def test_decision_router_reads_choice_result_from_state() -> None:
    from src.engine.graph_builder import _route_decision
    from src.engine.state import initial_state
    from src.engine.workflow import DecisionNode

    node = DecisionNode.model_validate(
        {
            "id": "d",
            "type": "decision",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "D",
                "mode": "choice",
                "instruction": "x?",
                "options": [{"label": "a"}, {"label": "b"}],
            },
        }
    )
    router = _route_decision(node)
    state = initial_state()
    state["node_results"]["d"] = {"output": {"decision": "b", "confidence": 0.5}}
    assert router(state) == "b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py -k decision -v`
Expected: FAIL (`_route_decision` doesn't exist; `"decision"` node type not handled)

- [ ] **Step 3: Implement the wiring**

In `src/engine/graph_builder.py`, add the executor import (alphabetically among the existing `from src.executors import (...)` block, ~line 39):

```python
from src.executors import (
    decision as _decision_executor,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
```

Add `DecisionNode` to the workflow-model import (~line 19-26):

```python
from src.engine.workflow import (
    DecisionNode,
    IfElseNode,
    UserApprovalNode,
    WhileNode,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
```

Update line 291's `_conditional_types` inside `validate_workflow_shape`:

```python
    _conditional_types = {"if-else", "while", "user-approval", "decision"}
```

Update line 310's module-level constant:

```python
CONDITIONAL_SOURCE_TYPES = {"if-else", "while", "user-approval", "decision"}
```

Add `_route_decision` right after `_route_user_approval` (~line 398, before `def build_graph`):

```python
def _route_decision(node: DecisionNode) -> Callable[[WorkflowStateDict], str]:
    """Router closure for a decision node. Reads the already-computed result
    from node_results (an LLM/judgment call isn't free or idempotent enough
    to repeat during routing, unlike if-else's simpleeval re-evaluation).
    Falls back to the first branch on a missing/malformed result so a
    provider hiccup can't wedge the graph.
    """

    def _fallback() -> str:
        if node.data.mode == "binary":
            return "false"
        assert node.data.options is not None
        return node.data.options[0].label

    def _router(state: WorkflowStateDict) -> str:
        node_results = state.get("node_results") or {}
        result = node_results.get(node.id)
        if not result or "output" not in result:
            return _fallback()
        decision = result["output"].get("decision")
        if node.data.mode == "binary":
            return "true" if decision else "false"
        assert node.data.options is not None
        labels = {o.label for o in node.data.options}
        if isinstance(decision, str) and decision in labels:
            return decision
        return _fallback()

    return _router
```

Update the normal-edge-skip check at line 428:

```python
        if source_node.type in {"if-else", "while", "user-approval", "decision"}:
```

Add the dispatch branch in the conditional-edges pass (~line 465, after the `elif node.type == "user-approval"` block):

```python
        elif node.type == "decision":
            assert isinstance(node, DecisionNode)
            if node.data.mode == "binary":
                required = {"true", "false"}
            else:
                assert node.data.options is not None
                required = {o.label for o in node.data.options}
            mapping = cast(
                "dict[Hashable, str]",
                _branch_mapping(node, list(workflow.edges), required),
            )
            builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                node.id, _route_decision(node), mapping
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py -v`
Expected: PASS (all tests, including the 5 new ones)

- [ ] **Step 5: Run the full backend test suite to confirm no regression**

Run: `.venv/Scripts/python -m pytest -m "not integration" -v`
Expected: PASS, same pass count as before plus the new tests from Tasks 1-5.

- [ ] **Step 6: Commit**

```bash
git add src/engine/graph_builder.py tests/unit/engine/test_graph_builder.py
git commit -m "feat(engine): wire decision node into graph_builder routing"
```

---

### Task 6: Frontend — `workflow-to-rf.ts` `CONDITIONAL_SOURCE_TYPES`

**Files:**
- Modify: `frontend/lib/workflow-to-rf.ts:26-30`

**Interfaces:** none (internal Set used by this file only).

This one has no existing dedicated test file (confirmed — `workflow-to-rf.ts` has no `.test.ts` in the repo); it's covered by Task 15's Playwright e2e test, which would fail silently (edges saved with `branch: null`) without this fix.

- [ ] **Step 1: Make the change**

```typescript
const CONDITIONAL_SOURCE_TYPES = new Set([
  "if-else",
  "while",
  "user-approval",
  "decision",
]);
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/workflow-to-rf.ts
git commit -m "fix(frontend): recognize decision as a conditional-source node type"
```

---

### Task 7: Frontend registration batch — palette, visuals, canvas branching, property panel dispatch

**Files:**
- Modify: `frontend/components/composer/canvas/node-visuals.ts` (imports ~line 14-42, `NODE_VISUALS` map ~line 63+)
- Modify: `frontend/components/composer/canvas/tools-palette.tsx:29` (append after if-else entry)
- Modify: `frontend/components/composer/canvas/workflow-canvas.tsx` (`BRANCH_SPECS`/`BranchingNode` ~line 142-204, `COMPOSER_NODE_TYPES` ~line 206-224)
- Modify: `frontend/components/composer/canvas/property-panel.tsx` (import ~line 17, `PANEL_MAP` ~line 64, `TYPE_LABELS` ~line 92 — panel import added once `decision.tsx` exists in Task 8, but the map entries are added now with a forward reference; see Step 2 note)
- Test: `frontend/components/composer/canvas/workflow-canvas.test.tsx`

**Interfaces:**
- Consumes: nothing new from other tasks except `DecisionPanel` (Task 8) for the `property-panel.tsx` import — see Step 2.

- [ ] **Step 1: Write the failing test for `BranchingNode`'s dynamic choice branches**

```typescript
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { COMPOSER_NODE_TYPES } from "./workflow-canvas";

function renderNode(type: string, data: Record<string, unknown>) {
  const NodeComponent = COMPOSER_NODE_TYPES[type];
  return render(
    <ReactFlowProvider>
      <NodeComponent id="d1" type={type} data={data} selected={false} />
    </ReactFlowProvider>
  );
}

describe("BranchingNode — decision choice mode", () => {
  it("renders one handle per configured option, not the static if-else pair", () => {
    const { container } = renderNode("decision", {
      mode: "choice",
      options: [{ label: "billing" }, { label: "technical" }, { label: "sales" }],
    });
    const handles = container.querySelectorAll(".react-flow__handle[data-handleid]");
    // 1 target handle (no data-handleid) + 3 labelled source handles.
    const labelledSourceHandles = Array.from(handles).filter((h) =>
      ["billing", "technical", "sales"].includes(h.getAttribute("data-handleid") ?? "")
    );
    expect(labelledSourceHandles).toHaveLength(3);
  });

  it("renders true/false handles for binary mode", () => {
    const { container } = renderNode("decision", { mode: "binary" });
    const handles = container.querySelectorAll(".react-flow__handle[data-handleid]");
    const ids = Array.from(handles).map((h) => h.getAttribute("data-handleid"));
    expect(ids).toEqual(expect.arrayContaining(["true", "false"]));
  });
});
```

Check `@xyflow/react`'s exact package name and whether `ReactFlowProvider` is the right wrapper by reading the top of `workflow-canvas.tsx`'s own imports before running — if the project uses a different React Flow import path (e.g. `reactflow` instead of `@xyflow/react`), match that instead; this is the one place in the plan asking you to verify an import path against the actual file rather than trusting the snippet, because React Flow's package was renamed across major versions and this repo's version wasn't independently re-verified for this step.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run workflow-canvas.test.tsx`
Expected: FAIL — `COMPOSER_NODE_TYPES["decision"]` is undefined.

- [ ] **Step 3: Implement the registration changes**

`node-visuals.ts` — add `Scale` to the lucide-react import list (~line 36, alphabetically before `Shield`):

```typescript
  Repeat,
  Scale,
  Shield,
```

Add an entry to `NODE_VISUALS` (anywhere in the map; placing it near `"if-else"` keeps related types together):

```typescript
  decision: {
    icon: Scale,
    iconWrapClass: "bg-purple-100 text-purple-700",
    accent: "text-purple-700",
    label: "Decision",
  },
```

`tools-palette.tsx` — add after the `"if-else"` entry (line 29):

```typescript
  { nodeType: "decision", label: "Decision" },
```

`workflow-canvas.tsx` — add `"decision"` to `BRANCH_SPECS` for binary mode's static case, and make `BranchingNode` compute choice-mode branches dynamically from `data`:

```typescript
const BRANCH_SPECS: Record<string, BranchSpec[]> = {
  "if-else": [
    { id: "true", label: "true", color: "rgb(16,185,129)" },
    { id: "false", label: "false", color: "rgb(244,63,94)" },
  ],
  while: [
    { id: "body", label: "body", color: "rgb(59,130,246)" },
    { id: "exit", label: "exit", color: "rgb(244,63,94)" },
  ],
  "user-approval": [
    { id: "approved", label: "approved", color: "rgb(16,185,129)" },
    { id: "rejected", label: "rejected", color: "rgb(244,63,94)" },
  ],
  decision: [
    { id: "true", label: "true", color: "rgb(16,185,129)" },
    { id: "false", label: "false", color: "rgb(244,63,94)" },
  ],
};

const CHOICE_BRANCH_COLORS = [
  "rgb(59,130,246)",
  "rgb(168,85,247)",
  "rgb(234,179,8)",
  "rgb(16,185,129)",
  "rgb(244,63,94)",
];

function BranchingNode({ id, data, type }: NodeProps<NodeData>) {
  const isDecisionChoice =
    type === "decision" && (data as Record<string, unknown>)?.mode === "choice";
  const branches: BranchSpec[] = isDecisionChoice
    ? (((data as Record<string, unknown>).options as { label: string }[]) ?? []).map(
        (opt, idx) => ({
          id: opt.label,
          label: opt.label,
          color: CHOICE_BRANCH_COLORS[idx % CHOICE_BRANCH_COLORS.length],
        })
      )
    : BRANCH_SPECS[type ?? ""] ?? [];
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <NodeChip type={type ?? "node"} id={id} data={data} />
      {branches.map((b, idx) => {
        const topPercent = ((idx + 1) * 100) / (branches.length + 1);
        return (
          <div
            key={b.id}
            style={{ position: "absolute", right: -4, top: `${topPercent}%` }}
          >
            <Handle
              id={b.id}
              type="source"
              position={Position.Right}
              style={{
                ...HANDLE_STYLE,
                background: b.color,
                position: "relative",
                top: 0,
                right: 0,
              }}
            />
            <span
              className="pointer-events-none absolute select-none rounded-sm bg-white/95 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-foreground shadow-sm"
              style={{
                left: 18,
                top: -2,
                color: b.color,
                border: `1px solid ${b.color}`,
              }}
            >
              {b.label}
            </span>
          </div>
        );
      })}
    </>
  );
}
```

Add `decision: BranchingNode` to `COMPOSER_NODE_TYPES` (~line 217, next to `"if-else": BranchingNode`):

```typescript
  "if-else": BranchingNode,
  decision: BranchingNode,
  while: BranchingNode,
```

`property-panel.tsx` — add the `PANEL_MAP` and `TYPE_LABELS` entries now, plus the import; this requires `decision.tsx` to exist, so if executing tasks strictly in order, do the import line as part of Task 8 instead and only add the map entries here as a forward reference is not possible in TypeScript — **reorder note: run Task 8 (create `decision.tsx`) before this file's edit, or fold this file's edit into Task 8's step list.** Either ordering is fine; the dependency is real (an import needs its target to exist), not a plan error — pick whichever your task-runner prefers and note it in the commit.

```typescript
import DecisionPanel from "./node-panels/decision";
```

```typescript
const PANEL_MAP: Record<string, PanelComponent> = {
  // ...
  "if-else": IfElsePanel,
  decision: DecisionPanel,
  while: WhilePanel,
  // ...
};
```

```typescript
const TYPE_LABELS: Record<string, string> = {
  // ...
  "if-else": "If / Else",
  decision: "Decision",
  while: "While",
  // ...
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run workflow-canvas.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npm test`
Expected: PASS, no regressions in existing panel/canvas tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-visuals.ts frontend/components/composer/canvas/tools-palette.tsx frontend/components/composer/canvas/workflow-canvas.tsx frontend/components/composer/canvas/workflow-canvas.test.tsx
git commit -m "feat(frontend): register decision node type and dynamic choice-mode branching"
```

(Leave `property-panel.tsx` uncommitted here if you deferred its edit to Task 8, per the reorder note above.)

---

### Task 8: `decision.tsx` panel (both providers)

**Files:**
- Create: `frontend/components/composer/canvas/node-panels/decision.tsx`
- Modify: `frontend/components/composer/canvas/property-panel.tsx` (import + `PANEL_MAP` + `TYPE_LABELS`, if not already done in Task 7)
- Test: `frontend/components/composer/canvas/node-panels/decision.test.tsx`
- Reference: `frontend/lib/api/llm-models.ts` (`listEnabledLlmModels`), `frontend/components/composer/canvas/node-panels/start.tsx` (structured-list-editor pattern), `frontend/components/composer/canvas/node-panels/if-else.tsx` (panel prop contract)

**Interfaces:**
- Consumes: `listEnabledLlmModels(provider?: string)` from `@/lib/api/llm-models`.
- Produces: default-exported `DecisionPanel({ data, onChange }: { data: Record<string, unknown>; onChange: (patch: Record<string, unknown>) => void })`.

- [ ] **Step 1: Write the failing tests**

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DecisionPanel from "./decision";

vi.mock("@/lib/api/llm-models", () => ({
  listEnabledLlmModels: vi.fn().mockResolvedValue([]),
}));

function baseData(overrides: Record<string, unknown> = {}) {
  return { mode: "binary", instruction: "Is this urgent?", ...overrides };
}

describe("DecisionPanel", () => {
  it("defaults provider to llm when unset", () => {
    render(<DecisionPanel data={baseData()} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue("LLM")).toBeInTheDocument();
  });

  it("switching provider to TypeSafe calls onChange with provider: typesafe", () => {
    const onChange = vi.fn();
    render(<DecisionPanel data={baseData()} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "typesafe" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ provider: "typesafe" }));
  });

  it("shows the zero-shot hint when examples is empty", () => {
    render(<DecisionPanel data={baseData()} onChange={vi.fn()} />);
    expect(screen.getByText(/Zero-shot decisions can be inconsistent/)).toBeInTheDocument();
  });

  it("hides the zero-shot hint once an example is added", () => {
    render(
      <DecisionPanel
        data={baseData({ examples: [{ input: "a", result: true }] })}
        onChange={vi.fn()}
      />
    );
    expect(screen.queryByText(/Zero-shot decisions can be inconsistent/)).not.toBeInTheDocument();
  });

  it("adding an example calls onChange with the new examples array", () => {
    const onChange = vi.fn();
    render(<DecisionPanel data={baseData()} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add example/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ examples: [{ input: "", result: false }] })
    );
  });

  it("choice mode shows the options editor, not true/false labels", () => {
    render(
      <DecisionPanel
        data={baseData({ mode: "choice", options: [{ label: "billing" }, { label: "technical" }] })}
        onChange={vi.fn()}
      />
    );
    expect(screen.getByDisplayValue("billing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add option/i })).toBeInTheDocument();
  });

  it("shows the TypeSafe examples-are-folded note only when provider is typesafe", () => {
    render(<DecisionPanel data={baseData({ provider: "typesafe" })} onChange={vi.fn()} />);
    expect(screen.getByText(/folded into criteria text/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run node-panels/decision.test.tsx`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `decision.tsx`**

```typescript
"use client";

import { useQuery } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { Plus, Trash2 } from "lucide-react";
import { listEnabledLlmModels } from "@/lib/api/llm-models";

type Mode = "binary" | "choice";

type DecisionExample = { input: string; result?: boolean; option?: string };
type DecisionOption = { label: string; description?: string };

function readExamples(data: Record<string, unknown>): DecisionExample[] {
  return Array.isArray(data.examples) ? (data.examples as DecisionExample[]) : [];
}

function readOptions(data: Record<string, unknown>): DecisionOption[] {
  return Array.isArray(data.options) ? (data.options as DecisionOption[]) : [];
}

export default function DecisionPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const mode = ((data.mode as Mode) ?? "binary") as Mode;
  const provider = (data.provider as string) ?? "llm";
  const examples = readExamples(data);
  const options = readOptions(data);

  const { data: models = [] } = useQuery({
    queryKey: ["llm-models", provider],
    queryFn: () => listEnabledLlmModels(provider),
  });

  function addExample() {
    onChange({ examples: [...examples, mode === "binary" ? { input: "", result: false } : { input: "", option: "" }] });
  }

  function updateExample(index: number, patch: Partial<DecisionExample>) {
    onChange({ examples: examples.map((e, i) => (i === index ? { ...e, ...patch } : e)) });
  }

  function removeExample(index: number) {
    onChange({ examples: examples.filter((_, i) => i !== index) });
  }

  function addOption() {
    onChange({ options: [...options, { label: "", description: "" }] });
  }

  function updateOption(index: number, patch: Partial<DecisionOption>) {
    onChange({ options: options.map((o, i) => (i === index ? { ...o, ...patch } : o)) });
  }

  function removeOption(index: number) {
    onChange({ options: options.filter((_, i) => i !== index) });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Mode</Label>
        <NativeSelect
          value={mode}
          onValueChange={(v) => onChange({ mode: v })}
          options={[
            { value: "binary", label: "Binary (yes/no)" },
            { value: "choice", label: "Choice (pick one)" },
          ]}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="decision-provider">Provider</Label>
        <NativeSelect
          id="decision-provider"
          aria-label="Provider"
          value={provider}
          onValueChange={(v) => onChange({ provider: v })}
          options={[
            { value: "llm", label: "LLM" },
            { value: "typesafe", label: "TypeSafe (Jev)" },
          ]}
        />
      </div>

      <div className="space-y-2">
        <Label>Instruction</Label>
        <Textarea
          value={(data.instruction as string) ?? ""}
          onChange={(e) => onChange({ instruction: e.target.value })}
          placeholder="Is this a refund request?"
        />
      </div>

      <div className="space-y-2">
        <Label>Model</Label>
        <NativeSelect
          value={(data.model as string) ?? ""}
          onValueChange={(v) => {
            const written = provider === "typesafe" ? v : `${provider}/${v}`;
            onChange({ model: written });
          }}
          options={models.map((m) => ({ value: m.modelId, label: m.label ?? m.modelId }))}
        />
      </div>

      <div className="space-y-2">
        <Label className="text-xs font-semibold uppercase text-muted-foreground">Examples</Label>
        {provider === "typesafe" && (
          <p className="text-xs text-muted-foreground">
            TypeSafe has no native few-shot mechanism — examples are folded into criteria text as
            illustrative guidance, not true few-shot.
          </p>
        )}
        {examples.length === 0 && (
          <p className="text-xs text-muted-foreground">
            Zero-shot decisions can be inconsistent on edge cases. Consider adding 1-2 examples.
          </p>
        )}
        {examples.map((ex, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={ex.input}
              onChange={(e) => updateExample(i, { input: e.target.value })}
              placeholder="Example text"
              className="h-7 text-xs"
            />
            {mode === "binary" ? (
              <NativeSelect
                value={String(ex.result ?? false)}
                onValueChange={(v) => updateExample(i, { result: v === "true" })}
                options={[
                  { value: "true", label: "true" },
                  { value: "false", label: "false" },
                ]}
              />
            ) : (
              <Input
                value={ex.option ?? ""}
                onChange={(e) => updateExample(i, { option: e.target.value })}
                placeholder="option label"
                className="h-7 text-xs"
              />
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => removeExample(i)}
              aria-label={`Remove example ${i + 1}`}
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        ))}
        <Button variant="outline" size="sm" className="w-full text-xs" onClick={addExample}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add example
        </Button>
      </div>

      {mode === "choice" && (
        <div className="space-y-2">
          <Label className="text-xs font-semibold uppercase text-muted-foreground">Options</Label>
          {options.map((opt, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input
                value={opt.label}
                onChange={(e) => updateOption(i, { label: e.target.value })}
                placeholder="Label"
                className="h-7 text-xs"
              />
              <Input
                value={opt.description ?? ""}
                onChange={(e) => updateOption(i, { description: e.target.value })}
                placeholder="Description (optional)"
                className="h-7 text-xs"
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => removeOption(i)}
                aria-label={`Remove option ${i + 1}`}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          ))}
          <Button variant="outline" size="sm" className="w-full text-xs" onClick={addOption}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add option
          </Button>
        </div>
      )}
    </div>
  );
}
```

Before wiring this in, check `@/components/ui/native-select`'s actual prop names (`value`/`onValueChange`/`options` was inferred from `start.tsx`'s usage — confirm against the component file itself, since a mismatch here would fail every dropdown in this panel silently different from how it fails in tests) and `@/components/ui/textarea`'s export name.

- [ ] **Step 4: Wire `property-panel.tsx` if not already done in Task 7**

(See Task 7 Step 3's reorder note — do it now if deferred.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run node-panels/decision.test.tsx`
Expected: PASS (7 tests)

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/decision.tsx frontend/components/composer/canvas/node-panels/decision.test.tsx frontend/components/composer/canvas/property-panel.tsx
git commit -m "feat(frontend): add Decision node Designer panel"
```

---

### Task 9: `TypeSafeJudgmentProvider`

**Files:**
- Modify: `src/llm/judgment.py` (append)
- Test: `tests/unit/llm/test_judgment.py` (append)

**Interfaces:**
- Consumes: `httpx`, `src.config.get_settings`.
- Produces: `TypeSafeJudgmentProvider` registered as `"typesafe"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/llm/test_judgment.py`:

```python
import httpx
import pytest

from src.engine.workflow import DecisionExample, DecisionOption
from src.llm.judgment import (
    JudgmentProviderError,
    TypeSafeJudgmentProvider,
    build_judgment_provider,
)


def test_build_judgment_provider_typesafe_returns_typesafe_provider():
    provider = build_judgment_provider("typesafe")
    assert isinstance(provider, TypeSafeJudgmentProvider)


async def test_typesafe_decide_binary_sends_noul_question(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import judgment as judgment_mod

    captured = {}

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    async def _fake_post(self, url, json, headers):
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


async def test_typesafe_decide_binary_low_noul_means_false_with_inverted_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.llm import judgment as judgment_mod

    def _fake_settings():
        class _S:
            typesafe_api_key = "sk-test"

        return _S()

    monkeypatch.setattr(judgment_mod, "get_settings", _fake_settings)

    async def _fake_post(self, url, json, headers):
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

    captured = {}

    async def _fake_post(self, url, json, headers):
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"answers": {"decision": {"type": "choice", "choice": "billing", "confidence": 0.8}}},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    option, confidence = await provider.decide_choice(
        instruction="Route it.",
        options=[DecisionOption(label="billing", description="money stuff"), DecisionOption(label="technical")],
        examples=[],
        text="I was overcharged",
        model=None,
    )
    assert option == "billing"
    assert confidence == 0.8
    q = captured["json"]["questions"]["decision"]
    assert q["type"] == "choice"
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

    async def _fake_post(self, url, json, headers):
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

    captured = {}

    async def _fake_post(self, url, json, headers):
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200, json={"answers": {"decision": {"type": "noul", "noul": 0.9}}}, request=request
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    provider = TypeSafeJudgmentProvider()
    await provider.decide_binary(instruction="x?", examples=[], text="y", model="jev-1.13.0")
    assert captured["json"]["model"] == "jev-1.13.0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/unit/llm/test_judgment.py -k typesafe -v`
Expected: FAIL — `TypeSafeJudgmentProvider` doesn't exist.

- [ ] **Step 3: Implement `TypeSafeJudgmentProvider`**

Append to `src/llm/judgment.py` (add `import httpx` and `from src.config import get_settings` to the top imports):

```python
import httpx

from src.config import get_settings
```

```python
_TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
_TYPESAFE_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _format_instructions(
    instruction: str, examples: list[DecisionExample], *, value_key: str
) -> str:
    if not examples:
        return instruction
    lines = [instruction, "", "Examples:"]
    for i, ex in enumerate(examples, start=1):
        value = ex.result if value_key == "result" else ex.option
        lines.append(f'{i}. "{ex.input}" -> {value}')
    return "\n".join(lines)


@register_judgment_provider("typesafe")
class TypeSafeJudgmentProvider:
    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        api_key = get_settings().typesafe_api_key or ""
        async with httpx.AsyncClient(timeout=_TYPESAFE_TIMEOUT) as client:
            resp = await client.post(
                _TYPESAFE_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}
            )
        if resp.status_code >= 400:
            raise JudgmentProviderError(
                f"TypeSafe HTTP {resp.status_code}: {resp.text[:240]}"
            )
        return resp.json()

    async def decide_binary(
        self, *, instruction: str, examples: list[DecisionExample], text: str, model: str | None
    ) -> tuple[bool, float]:
        body: dict[str, Any] = {
            "state": text,
            "questions": {
                "decision": {
                    "type": "noul",
                    "instructions": _format_instructions(instruction, examples, value_key="result"),
                }
            },
        }
        if model:
            body["model"] = model
        data = await self._post(body)
        noul = data["answers"]["decision"]["noul"]
        result = noul >= 0.5
        confidence = noul if result else 1 - noul
        return result, confidence

    async def decide_choice(
        self,
        *,
        instruction: str,
        options: list[DecisionOption],
        examples: list[DecisionExample],
        text: str,
        model: str | None,
    ) -> tuple[str, float]:
        criteria = {opt.label: opt.description for opt in options if opt.description}
        question: dict[str, Any] = {
            "type": "choice",
            "instructions": _format_instructions(instruction, examples, value_key="option"),
        }
        if criteria:
            question["criteria"] = criteria
        body: dict[str, Any] = {"state": text, "questions": {"decision": question}}
        if model:
            body["model"] = model
        data = await self._post(body)
        answer = data["answers"]["decision"]
        return answer["choice"], answer["confidence"]
```

Add `TypeSafeJudgmentProvider` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/unit/llm/test_judgment.py -v`
Expected: PASS (all tests from Task 3 + Task 9)

- [ ] **Step 5: Commit**

```bash
git add src/llm/judgment.py tests/unit/llm/test_judgment.py
git commit -m "feat(llm): add TypeSafeJudgmentProvider"
```

---

### Task 10: Admin backend key plumbing — `config.py`, `key_sync.py`, `cli/keys.py`, `admin_llm_keys.py`, `.env.example`

**Files:**
- Modify: `src/config.py`
- Modify: `src/security/key_sync.py:41-55`
- Modify: `src/cli/keys.py:15-29`
- Modify: `src/api/admin_llm_keys.py:20-34`
- Modify: `.env.example:52-55`
- Test: `tests/unit/security/test_key_sync.py` (or the existing equivalent — check the file name first) and `tests/unit/cli/test_keys.py`, extended in place

**Interfaces:** none new — this batches dict-literal-only additions to four already-tested files.

- [ ] **Step 1: Write the failing tests**

Find the existing test file(s) covering `PROVIDER_TO_SETTINGS_FIELD` and `_PROVIDER_TO_ENV` (likely `tests/unit/security/test_key_sync.py` and `tests/unit/cli/test_keys.py` — confirm exact names with `Glob` before writing, since this plan's earlier research didn't open them). Add:

```python
def test_typesafe_in_provider_to_settings_field():
    from src.security.key_sync import PROVIDER_TO_SETTINGS_FIELD

    assert PROVIDER_TO_SETTINGS_FIELD["typesafe"] == "typesafe_api_key"
```

```python
def test_typesafe_in_provider_to_env():
    from src.cli.keys import _PROVIDER_TO_ENV

    assert _PROVIDER_TO_ENV["typesafe"] == "TYPESAFE_API_KEY"
```

And in whichever test file covers `admin_llm_keys.py` (e.g. `tests/unit/api/test_admin_llm_keys.py`):

```python
def test_typesafe_is_an_allowed_provider():
    from src.api.admin_llm_keys import _ALLOWED_PROVIDERS

    assert "typesafe" in _ALLOWED_PROVIDERS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest -k typesafe -v`
Expected: FAIL (KeyError / assertion failures)

- [ ] **Step 3: Make the four changes**

`src/config.py` — add near the other `*_api_key` fields on `Settings`:

```python
    typesafe_api_key: str | None = None
```

`src/security/key_sync.py:41-55` — add to `PROVIDER_TO_SETTINGS_FIELD`:

```python
    "typesafe": "typesafe_api_key",
```

`src/cli/keys.py:15-29` — add to `_PROVIDER_TO_ENV`:

```python
    "typesafe": "TYPESAFE_API_KEY",
```

`src/api/admin_llm_keys.py:20-34` — add to `_ALLOWED_PROVIDERS`:

```python
    "typesafe",
```

`.env.example:52-55` — add after the existing four LLM provider keys, and mention it in the provisioning comment block above them:

```
#   TYPESAFE_API_KEY  — https://console.typesafe.ai/keys
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=
GROQ_API_KEY=
TYPESAFE_API_KEY=
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -k typesafe -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `.venv/Scripts/python -m pytest -m "not integration" -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/config.py src/security/key_sync.py src/cli/keys.py src/api/admin_llm_keys.py .env.example
git commit -m "feat(admin): add TypeSafe to the LLM key CRUD provider registries"
```

(Include whichever existing test file(s) you extended in Step 1 in this commit too.)

---

### Task 11: `_test_typesafe` in `admin_llm_keys_test.py`

**Files:**
- Modify: `src/api/admin_llm_keys_test.py` (add function + `_TESTERS` entry ~line 260)
- Test: extend the existing test file for this module (find via `Glob "tests/**/test_admin_llm_keys_test*"` or similar — confirm the exact path before writing)

- [ ] **Step 1: Write the failing test**

```python
import httpx
import pytest

from src.api.admin_llm_keys_test import run_key_test


async def test_typesafe_key_test_ok_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import admin_llm_keys_test as mod

    async def _fake_post(url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"answers": {}}, request=request)

    monkeypatch.setattr(mod, "_post", _fake_post)
    result = await run_key_test("typesafe", "sk-test")
    assert result.ok is True


async def test_typesafe_key_test_fails_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import admin_llm_keys_test as mod

    async def _fake_post(url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(401, text="bad key", request=request)

    monkeypatch.setattr(mod, "_post", _fake_post)
    result = await run_key_test("typesafe", "sk-bad")
    assert result.ok is False
    assert result.status == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest -k typesafe_key_test -v`
Expected: FAIL — `run_key_test("typesafe", ...)` returns `ok=False, message="no tester for provider 'typesafe'"`.

- [ ] **Step 3: Implement `_test_typesafe`**

Add before the `_TESTERS` dict:

```python
async def _test_typesafe(key: str) -> KeyTestResult:
    resp = await _post(
        "https://api.typesafe.ai/v1/systemone",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={
            "state": "ping",
            "questions": {"probe": {"type": "noul", "instructions": "Is this text non-empty?"}},
        },
    )
    if resp.status_code == 200:
        return KeyTestResult(ok=True, status=200, message="TypeSafe key valid.")
    return KeyTestResult(
        ok=False,
        status=resp.status_code,
        message=f"TypeSafe HTTP {resp.status_code}: {resp.text[:200]}",
    )
```

Add to `_TESTERS`:

```python
    "typesafe": _test_typesafe,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest -k typesafe_key_test -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/admin_llm_keys_test.py
git commit -m "feat(admin): add TypeSafe key test-connection probe"
```

---

### Task 12: `_verify_typesafe` in `admin_llm_models_verify.py`

**Files:**
- Modify: `src/api/admin_llm_models_verify.py` (add function + `_VERIFIERS` entry ~line 253)
- Test: extend the existing test file for this module (confirm exact path via `Glob`)

- [ ] **Step 1: Write the failing test**

```python
import httpx
import pytest

from src.api.admin_llm_models_verify import run_model_verify


async def test_verify_typesafe_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import admin_llm_models_verify as mod

    async def _fake_post(url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"answers": {}}, request=request)

    monkeypatch.setattr(mod, "_post", _fake_post)
    result = await run_model_verify("typesafe", "jev-latest", "sk-test")
    assert result.status == "ok"


async def test_verify_typesafe_unavailable_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import admin_llm_models_verify as mod

    async def _fake_post(url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(404, text="model not found", request=request)

    monkeypatch.setattr(mod, "_post", _fake_post)
    result = await run_model_verify("typesafe", "jev-nonexistent", "sk-test")
    assert result.status == "unavailable"


async def test_verify_typesafe_auth_error_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import admin_llm_models_verify as mod

    async def _fake_post(url, headers, json):
        request = httpx.Request("POST", url)
        return httpx.Response(401, text="bad key", request=request)

    monkeypatch.setattr(mod, "_post", _fake_post)
    result = await run_model_verify("typesafe", "jev-latest", "sk-bad")
    assert result.status == "auth_error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest -k verify_typesafe -v`
Expected: FAIL — `run_model_verify("typesafe", ...)` returns `status="error", message="no verifier for provider 'typesafe'"`.

- [ ] **Step 3: Implement `_verify_typesafe`**

Add before the `_VERIFIERS` dict:

```python
async def _verify_typesafe(model_id: str, key: str) -> ModelVerifyResult:
    resp = await _post(
        "https://api.typesafe.ai/v1/systemone",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={
            "state": "ping",
            "model": model_id,
            "questions": {"probe": {"type": "noul", "instructions": "Is this text non-empty?"}},
        },
    )
    status = _classify(resp.status_code, resp.text)
    msg = (
        f"TypeSafe {model_id!r} OK."
        if status == "ok"
        else f"TypeSafe HTTP {resp.status_code}: {resp.text[:240]}"
    )
    return ModelVerifyResult(status=status, http_status=resp.status_code, message=msg)
```

Add to `_VERIFIERS`:

```python
    "typesafe": _verify_typesafe,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -k verify_typesafe -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/admin_llm_models_verify.py
git commit -m "feat(admin): add TypeSafe model-verify probe"
```

---

### Task 13: `_DB_ONLY_PROVIDERS` in `llm_models_live.py`

**Files:**
- Modify: `src/api/llm_models_live.py` (add set + gate change, ~line 152 and ~line 313-330)
- Test: extend the existing test file for this module (confirm exact path via `Glob`)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing tests**

```python
import pytest


async def test_typesafe_skips_live_fetch_and_uses_db_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api import llm_models_live as mod

    called = {"live": False}

    async def _fake_fetch_live(provider):
        called["live"] = True
        raise AssertionError("should not be called for a DB-only provider")

    monkeypatch.setattr(mod, "_fetch_live", _fake_fetch_live)

    async def _fake_db_fallback(db, provider):
        return []

    monkeypatch.setattr(mod, "_db_fallback", _fake_db_fallback)

    from unittest.mock import AsyncMock

    fake_db = AsyncMock()
    response = await mod.list_available_models.__wrapped__(  # type: ignore[attr-defined]
        provider="typesafe", refresh=False, db=fake_db, _user_id="u1"
    )
    assert called["live"] is False
    assert response.provider == "typesafe"


def test_typesafe_not_in_live_providers_table():
    from src.api.llm_models_live import _PROVIDERS, _DB_ONLY_PROVIDERS

    assert "typesafe" not in _PROVIDERS
    assert "typesafe" in _DB_ONLY_PROVIDERS
```

If `list_available_models.__wrapped__` isn't the right way to call a FastAPI route function directly in this codebase's existing tests, check how the existing tests for this endpoint call it (Glob for the test file first) and match that pattern instead — this is a case where the plan's exact call mechanics may not match the project's established test-client convention, so verify against a neighboring test before trusting the snippet.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest -k typesafe -v` (scoped to this module's test file)
Expected: FAIL — `_DB_ONLY_PROVIDERS` doesn't exist; `provider not in _PROVIDERS` raises 422 for `"typesafe"`.

- [ ] **Step 3: Implement the change**

Add near `_PROVIDERS` (~line 195, after its closing brace):

```python
_DB_ONLY_PROVIDERS: set[str] = {"typesafe"}
```

Update the route handler's gate and fetch logic (~line 326 and the `try`/`except` around `_fetch_live`):

```python
    if provider not in _PROVIDERS and provider not in _DB_ONLY_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported provider: {provider}",
        )

    now = time.monotonic()
    if not refresh:
        cached = _cache.get(provider)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return AvailableModelsResponse(provider=provider, models=cached[1])

    async with _cache_lock:
        if not refresh:
            cached = _cache.get(provider)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                return AvailableModelsResponse(provider=provider, models=cached[1])
        if provider in _DB_ONLY_PROVIDERS:
            fallback = await _db_fallback(db, provider)
            return AvailableModelsResponse(provider=provider, models=fallback)
        try:
            models = await _fetch_live(provider)
            _cache[provider] = (now, models)
            return AvailableModelsResponse(provider=provider, models=models)
        except Exception as exc:
            logger.warning(
                "live model fetch failed for %s — falling back to DB. %s",
                provider,
                exc,
            )
            fallback = await _db_fallback(db, provider)
            return AvailableModelsResponse(provider=provider, models=fallback)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest -k typesafe -v` (scoped to this module's test file)
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `.venv/Scripts/python -m pytest -m "not integration" -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/api/llm_models_live.py
git commit -m "feat(admin): serve TypeSafe models from DB-only fallback (no live /models endpoint)"
```

---

### Task 14: Admin frontend pages — `llm-keys/page.tsx`, `llm-models/page.tsx`

**Files:**
- Modify: `frontend/app/admin/llm-keys/page.tsx:33-39`
- Modify: `frontend/app/admin/llm-models/page.tsx:39-45`
- Test: extend whichever test files (if any) cover these pages — confirm via `Glob "frontend/app/admin/**/*.test.tsx"` before writing; if none exist, this task has no dedicated frontend test, matching the codebase's existing convention for these two pages (verify that convention holds by checking the Glob result, not by assuming it).

- [ ] **Step 1: Make the changes**

`frontend/app/admin/llm-keys/page.tsx:33-39`:

```typescript
const PROVIDERS = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "google", label: "Google" },
  { id: "groq", label: "Groq" },
  { id: "deepseek", label: "DeepSeek" },
  { id: "qwen", label: "Qwen" },
  { id: "typesafe", label: "TypeSafe (Jev)" },
];
```

`frontend/app/admin/llm-models/page.tsx:39-45`:

```typescript
const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "groq", label: "Groq" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "qwen", label: "Qwen" },
  { value: "typesafe", label: "TypeSafe (Jev)" },
];
```

- [ ] **Step 2: Manually verify in the running app**

Run: `cd frontend && npm run dev`, sign in as an admin, open Admin → LLM keys — confirm a "TypeSafe (Jev)" row appears and its key can be saved/tested. Open Admin → LLM models → Add model — confirm "TypeSafe (Jev)" appears in the provider dropdown and, with manual mode, a model id like `jev-latest` can be added and (once a real key is set) verified.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/admin/llm-keys/page.tsx frontend/app/admin/llm-models/page.tsx
git commit -m "feat(admin): expose TypeSafe in both admin LLM catalog pages"
```

---

### Task 15: Playwright e2e — binary Decision node flow

**Files:**
- Create or extend: `frontend/e2e/decision-node.spec.ts` (match the existing Playwright spec file location/naming convention — confirm via `Glob "frontend/e2e/**/*.spec.ts"` before creating a new file)

**Interfaces:** none new — this is an end-to-end smoke test exercising everything built in Tasks 1-8.

- [ ] **Step 1: Write the e2e test**

```typescript
import { test, expect } from "@playwright/test";
import { createTestUser, cleanupTestUsers } from "./helpers/test-users"; // match actual helper path/name in the repo

test.describe("Decision node — binary mode", () => {
  let userEmail: string;

  test.afterEach(async () => {
    await cleanupTestUsers();
  });

  test("wiring both branches and running the workflow takes the correct branch", async ({ page }) => {
    const user = await createTestUser();
    userEmail = user.email;
    await page.goto("/login");
    // ... sign in as user, matching the pattern in an existing e2e spec (e.g. if-else or
    // guardrails' e2e test, if one exists — check for it before writing this test from scratch)
    await page.goto("/designer/new");
    // Drag a Decision node onto the canvas, set mode=binary, instruction="Always answer yes",
    // add one example ("anything", true), wire both true/false branches to two End nodes with
    // distinguishable labels, save, run the workflow, and assert the execution's final output
    // matches the "true" branch's End node.
    //
    // This test's exact selectors depend on the Designer's existing drag-and-drop and node-panel
    // test IDs, which weren't inventoried as part of this plan — before writing the real
    // assertions, open an existing e2e spec that already drives node placement (e.g. one for
    // if-else or guardrails, whichever exists) and mirror its selectors rather than inventing new
    // ones.
  });
});
```

This task's test body is intentionally left as a structural skeleton with a clear pointer to the pattern to copy, rather than invented selectors that would silently not match the real Designer DOM — write the real assertions by reading whichever existing e2e spec drives node placement most closely (if-else's, if one exists) and adapting it, not by guessing.

- [ ] **Step 2: Run the e2e test**

Run: `cd frontend && npx playwright test decision-node.spec.ts`
Expected: PASS once selectors are filled in against the real Designer DOM.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/decision-node.spec.ts
git commit -m "test(e2e): add Decision node binary-mode workflow smoke test"
```

---

## Self-Review

**Spec coverage:** Data model (Task 1) · isolation boundary (Tasks 2, and enforced by omission everywhere else) · `JudgmentProvider` + `LLMJudgmentProvider` (Task 3) · `DecisionExecutor` (Task 4) · routing + all four `CONDITIONAL_SOURCE_TYPES` spots including the frontend one (Tasks 5-6) · palette/visuals/canvas/property-panel registration + dynamic choice branching (Task 7) · Designer panel with both providers, examples/options editors, zero-shot hint, TypeSafe fold-note (Task 8) · `TypeSafeJudgmentProvider` (Task 9) · full admin key/model catalog parity across both backend and both frontend pages (Tasks 10-14) · e2e smoke test (Task 15). No spec section is without a task.

**Placeholder scan:** No TBD/TODO left in any step; the three places this plan explicitly asks the implementer to verify something against the live codebase before trusting the snippet (React Flow's import path in Task 7, `native-select`/`textarea` prop names in Task 8, and Task 15's e2e selectors) are flagged as verification steps with a stated reason, not vague hand-waves — the plan doesn't know those exact shapes without reading files this research pass didn't open, and says so rather than guessing.

**Type consistency:** `JudgmentProvider.decide_binary`/`decide_choice` signatures match between the Protocol (Task 3), `LLMJudgmentProvider` (Task 3), `TypeSafeJudgmentProvider` (Task 9), and `DecisionExecutor`'s call sites (Task 4). `DecisionNodeData`'s field names (`mode`, `instruction`, `examples`, `options`, `model`, `provider`) are used identically across Tasks 1, 4, 5, and 8. `build_judgment_provider`/`register_judgment_provider` names match between Task 3's definition and Tasks 4, 9's usage.
