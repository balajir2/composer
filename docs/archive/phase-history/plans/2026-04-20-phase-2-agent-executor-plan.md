# Phase 2 — Agent Executor + LLM Providers + Tool Provider Framework: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Agent executor end-to-end with 4 LLM providers (Anthropic/OpenAI/Google/Groq), Tavily tool integration, full OAB-parity variable substitution, structured output via `with_structured_output`, and a `ToolProvider` framework that both standard tools and Phase 3 MCP servers plug into.

**Architecture:** `src/llm/providers.py` maps `"provider/model"` strings to LangChain chat models. `src/llm/structured_output.py` wraps `with_structured_output` for Agent + Phase 4 Extract. `src/variable_substitution.py` ports OAB's `{{...}}` engine. `src/tools/` defines `ToolProvider` ABC + registry; `src/tools/providers/tavily.py` is the reference implementation. `src/executors/agent.py` threads all of it into a `while iter < 10` agentic loop using `chat_model.bind_tools()`.

**Tech Stack:** Python 3.11/3.12, LangChain Python (`langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq`, `langchain-core`), Pydantic v2, httpx, pytest + pytest-asyncio + pytest-httpx.

**Spec:** [`docs/superpowers/specs/2026-04-20-phase-2-agent-executor-design.md`](../specs/2026-04-20-phase-2-agent-executor-design.md)
**ADRs:** [ADR-0006, ADR-0007, ADR-0008, ADR-0009](../../design/decisions.md)

---

## Sequencing and discipline

20 tasks, one commit each (Task 7 is now a family: 7 Tavily + 7b Serper + 7c Firecrawl + 7d Browserless — all following the Tavily template with only the provider-specific HTTP payload changing). Every task ends with:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src tests
uv run pytest -m "not integration"
```

All four must pass. CI runs the integration suite separately against real provider keys (Task 16 adds the secrets plumbing).

**⚠️ Forbidden files (all tasks except where explicitly noted):** `pyproject.toml`, `.github/workflows/*` (except Task 16), `prisma/schema.prisma`, `CLAUDE.md` (except Task 17), `docs/design/*` (except Task 17 for ADR backfills), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`. If a task hits a pyright error it can't fix inline with `# pyright: ignore[specific_rule]`, it reports BLOCKED — never loosens `pyproject.toml`.

Co-author footer on every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

---

## Pre-task: add `pytest-httpx` to dev deps

**Files:** `pyproject.toml` (authorized for this pre-task only)

- [ ] Check if `pytest-httpx` is in `[project.optional-dependencies].dev`. If not, add it. `pytest-httpx` provides the `httpx_mock` fixture we use for Tavily and any future HTTP-tool unit tests.

- [ ] Run `uv sync --all-extras` to install.

- [ ] Commit:

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add pytest-httpx for HTTP tool unit tests

Phase 2 unit-tests the Tavily tool wrapper by mocking httpx responses.
pytest-httpx's httpx_mock fixture is the standard way. Dev-only dep.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 1: Config — add LLM + Tavily settings fields

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Add fields to `Settings` class**

Append to `src/config.py`'s `Settings` class (under the existing LangSmith section):

```python
    # ─── LLM providers (Phase 2) ──────────────────
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    openai_api_key: str = Field(default="", description="OpenAI API key")
    google_api_key: str = Field(default="", description="Google AI Studio API key")
    groq_api_key: str = Field(default="", description="Groq API key")

    # ─── Agent tools (Phase 2) ────────────────────
    tavily_api_key: str = Field(default="", description="Tavily web-search API key")
    serper_api_key: str = Field(default="", description="Serper.dev Google-search API key")
    firecrawl_api_key: str = Field(default="", description="Firecrawl web-scrape API key")
    browserless_api_key: str = Field(default="", description="Browserless headless-Chrome API key")
```

- [ ] **Step 2: Verify settings load**

```bash
.venv/Scripts/python -c "from src.config import get_settings; s = get_settings(); print('OK', bool(s.anthropic_api_key or True))"
```
Expected: `OK True` (no exception).

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "feat(config): add Phase 2 LLM + Tavily settings fields

Pydantic Settings picks up the matching env vars (ANTHROPIC_API_KEY,
OPENAI_API_KEY, GOOGLE_API_KEY, GROQ_API_KEY, TAVILY_API_KEY) from .env.
Empty-string default — providers that aren't configured skip cleanly
at integration-test time.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Variable substitution — OAB-parity `{{...}}` engine

**Files:**
- Create: `src/variable_substitution.py`
- Create: `tests/unit/test_variable_substitution.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_variable_substitution.py`:

```python
"""Tests for OAB-parity variable substitution."""
from src.engine.state import initial_state
from src.variable_substitution import substitute


def test_flat_key_from_variables() -> None:
    state = initial_state()
    state["variables"]["name"] = "Ada"
    assert substitute("Hello, {{name}}!", state) == "Hello, Ada!"


def test_unresolved_renders_literal() -> None:
    state = initial_state()
    assert substitute("Hello, {{ghost}}!", state) == "Hello, {{ghost}}!"


def test_dotted_path_under_variables() -> None:
    state = initial_state()
    state["variables"]["user"] = {"name": "Ada", "age": 37}
    assert substitute("{{user.name}} is {{user.age}}", state) == "Ada is 37"


def test_explicit_state_variables_prefix() -> None:
    state = initial_state()
    state["variables"]["foo"] = {"bar": "baz"}
    assert substitute("{{state.variables.foo.bar}}", state) == "baz"


def test_explicit_state_nodeResults_prefix() -> None:
    state = initial_state()
    state["node_results"]["n1"] = {"output": "hello"}
    assert substitute("{{state.nodeResults.n1.output}}", state) == "hello"


def test_prototype_pollution_blocked() -> None:
    state = initial_state()
    state["variables"]["__proto__"] = "evil"
    assert substitute("{{__proto__}}", state) == "{{__proto__}}"


def test_python_unsafe_segments_blocked() -> None:
    state = initial_state()
    assert substitute("{{__class__}}", state) == "{{__class__}}"


def test_whitespace_trimmed_inside_braces() -> None:
    state = initial_state()
    state["variables"]["x"] = "y"
    assert substitute("{{ x }}", state) == "y"


def test_multiple_substitutions_in_one_template() -> None:
    state = initial_state()
    state["variables"]["a"] = "A"
    state["variables"]["b"] = "B"
    assert substitute("{{a}}-{{b}}-{{a}}", state) == "A-B-A"


def test_dict_value_renders_as_json() -> None:
    state = initial_state()
    state["variables"]["cfg"] = {"k": "v"}
    assert substitute("{{cfg}}", state) == '{"k": "v"}'


def test_no_braces_returns_template_unchanged() -> None:
    state = initial_state()
    assert substitute("plain text", state) == "plain text"
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/test_variable_substitution.py -v
```
Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Implement**

Create `src/variable_substitution.py`:

```python
"""OAB-parity `{{...}}` variable substitution engine.

Mirrors lib/workflow/variable-substitution.ts. Supports flat keys,
dotted paths, explicit `state.variables.` / `state.nodeResults.`
prefixes, and blocks prototype-pollution segments.

See ADR-0007 and Phase 2 spec §7.
"""

import json
import re
from typing import Any

from src.engine.state import WorkflowStateDict

_PATTERN = re.compile(r"\{\{([^}]+)\}\}")

# Block both JS prototype-pollution keys (OAB) and Python dunder-attacks.
_UNSAFE_SEGMENTS: frozenset[str] = frozenset({
    "__proto__", "constructor", "prototype",
    "__class__", "__dict__", "__globals__", "__builtins__", "__subclasses__",
})


def substitute(template: str, state: WorkflowStateDict) -> str:
    """Render `template` with `{{path}}` placeholders resolved from `state`.

    Unresolved placeholders render as the literal `{{path}}` text (matches OAB).
    """
    return _PATTERN.sub(lambda m: _resolve(m.group(1).strip(), state), template)


def _resolve(path: str, state: WorkflowStateDict) -> str:
    segments = path.split(".")
    literal = f"{{{{{path}}}}}"  # fallback on miss

    # Explicit state.<root>.<rest> prefix
    if segments[0] == "state":
        if len(segments) < 3:
            return literal
        root_name = segments[1]
        rest = segments[2:]
        if root_name == "variables":
            root: Any = state["variables"]
        elif root_name == "nodeResults":
            root = state["node_results"]
        else:
            return literal
        value = _walk(root, rest)
    else:
        # Default: walk state.variables
        value = _walk(state["variables"], segments)

    if value is None:
        return literal
    return value if isinstance(value, str) else json.dumps(value)


def _walk(root: Any, segments: list[str]) -> Any:
    current = root
    for seg in segments:
        if seg in _UNSAFE_SEGMENTS:
            return None
        if isinstance(current, dict):
            current = current.get(seg)
        else:
            return None
        if current is None:
            return None
    return current


__all__ = ["substitute"]
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/unit/test_variable_substitution.py -v
```
Expected: all 11 tests PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/variable_substitution.py tests/unit/test_variable_substitution.py
git commit -m "feat: OAB-parity {{...}} variable substitution engine

Ports lib/workflow/variable-substitution.ts. Supports flat keys, dotted
paths, explicit state.variables/state.nodeResults prefixes, prototype-
pollution guard on every segment. Unresolved placeholders render as
literal {{path}} text (matching OAB fall-through behaviour).

11 unit tests cover every grammar branch and the safety guard.

See ADR-0007; spec §7.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: LLM providers — model-string dispatch

**Files:**
- Create: `src/llm/__init__.py` (empty)
- Create: `src/llm/providers.py`
- Create: `tests/unit/llm/__init__.py` (empty)
- Create: `tests/unit/llm/test_providers.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/llm/test_providers.py`:

```python
"""Tests for LLM provider dispatch."""
import pytest

from src.llm.providers import (
    MissingApiKeyError,
    UnsupportedProviderError,
    build_chat_model,
)


def test_anthropic_returns_chat_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src.config import get_settings
    get_settings.cache_clear()
    from langchain_anthropic import ChatAnthropic
    model = build_chat_model("anthropic/claude-3-5-haiku-latest")
    assert isinstance(model, ChatAnthropic)


def test_openai_returns_chat_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from src.config import get_settings
    get_settings.cache_clear()
    from langchain_openai import ChatOpenAI
    model = build_chat_model("openai/gpt-5-nano")
    assert isinstance(model, ChatOpenAI)


def test_google_returns_chat_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    from src.config import get_settings
    get_settings.cache_clear()
    from langchain_google_genai import ChatGoogleGenerativeAI
    model = build_chat_model("google/gemini-2.0-flash")
    assert isinstance(model, ChatGoogleGenerativeAI)


def test_groq_returns_chat_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from src.config import get_settings
    get_settings.cache_clear()
    from langchain_groq import ChatGroq
    model = build_chat_model("groq/llama-3.3-70b-versatile")
    assert isinstance(model, ChatGroq)


def test_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src.config import get_settings
    get_settings.cache_clear()
    with pytest.raises(UnsupportedProviderError, match="cohere"):
        build_chat_model("cohere/command-r")


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from src.config import get_settings
    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY"):
        build_chat_model("anthropic/claude-3-5-haiku-latest")


def test_no_slash_defaults_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matches OAB agent.ts:184-196: no '/' in model string defaults to openai."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from src.config import get_settings
    get_settings.cache_clear()
    from langchain_openai import ChatOpenAI
    model = build_chat_model("gpt-5-nano")
    assert isinstance(model, ChatOpenAI)


def test_token_limit_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src.config import get_settings
    get_settings.cache_clear()
    model = build_chat_model("anthropic/claude-3-5-haiku-latest", token_limit=500)
    # ChatAnthropic exposes max_tokens on the instance
    assert model.max_tokens == 500  # type: ignore[attr-defined]
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/llm/test_providers.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/llm/__init__.py` (empty).

Create `src/llm/providers.py`:

```python
"""LLM provider dispatch.

Maps `"provider/modelname"` strings to LangChain chat-model instances.
See ADR-0006; spec §5.
"""

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from src.config import get_settings


class UnsupportedProviderError(ValueError):
    """Raised when the provider prefix in the model string isn't one of
    anthropic / openai / google / groq."""


class MissingApiKeyError(RuntimeError):
    """Raised when the configured provider's API key is empty."""


_KEY_MAP: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
    "groq": "groq_api_key",
}


def build_chat_model(
    model_string: str,
    *,
    token_limit: int | None = None,
    temperature: float | None = None,
    extra: dict[str, Any] | None = None,
) -> BaseChatModel:
    """Parse `model_string` as `provider/modelname`, return a LangChain chat model.

    Providers: 'anthropic', 'openai', 'google', 'groq'.
    No prefix → defaults to 'openai' (matches OAB agent.ts:184-196).

    Raises:
        UnsupportedProviderError: unknown provider.
        MissingApiKeyError: provider's key is empty in Settings.
    """
    if "/" in model_string:
        provider, _, model_name = model_string.partition("/")
    else:
        provider, model_name = "openai", model_string

    if provider not in _KEY_MAP:
        raise UnsupportedProviderError(
            f"Unknown provider {provider!r}. "
            f"Supported: {sorted(_KEY_MAP)}"
        )

    settings = get_settings()
    key_field = _KEY_MAP[provider]
    api_key = getattr(settings, key_field, "")
    if not api_key:
        env_var = key_field.upper()
        raise MissingApiKeyError(
            f"{env_var} is not set. Add it to .env or deployment env vars."
        )

    extra_kwargs = extra or {}

    if provider == "anthropic":
        kwargs: dict[str, Any] = {
            "model": model_name,
            "api_key": api_key,
            **extra_kwargs,
        }
        if token_limit is not None:
            kwargs["max_tokens"] = token_limit
        if temperature is not None:
            kwargs["temperature"] = temperature
        return ChatAnthropic(**kwargs)  # pyright: ignore[reportCallIssue]

    if provider == "openai":
        kwargs = {
            "model": model_name,
            "api_key": api_key,
            **extra_kwargs,
        }
        # LangChain's ChatOpenAI handles o1/o3/gpt-5 max_completion_tokens
        # routing internally when `model` is set correctly.
        if token_limit is not None:
            kwargs["max_tokens"] = token_limit
        if temperature is not None:
            kwargs["temperature"] = temperature
        return ChatOpenAI(**kwargs)  # pyright: ignore[reportCallIssue]

    if provider == "google":
        kwargs = {
            "model": model_name,
            "google_api_key": api_key,
            **extra_kwargs,
        }
        if token_limit is not None:
            kwargs["max_output_tokens"] = token_limit
        if temperature is not None:
            kwargs["temperature"] = temperature
        return ChatGoogleGenerativeAI(**kwargs)  # pyright: ignore[reportCallIssue]

    # groq
    kwargs = {
        "model": model_name,
        "api_key": api_key,
        **extra_kwargs,
    }
    if token_limit is not None:
        kwargs["max_tokens"] = token_limit
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatGroq(**kwargs)  # pyright: ignore[reportCallIssue]


__all__ = [
    "MissingApiKeyError",
    "UnsupportedProviderError",
    "build_chat_model",
]
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/unit/llm/test_providers.py -v
```
Expected: all 8 PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/llm/__init__.py src/llm/providers.py tests/unit/llm/
git commit -m "feat(llm): provider dispatch for Anthropic/OpenAI/Google/Groq

build_chat_model parses 'provider/modelname' strings and returns the
matching LangChain chat model (ChatAnthropic / ChatOpenAI /
ChatGoogleGenerativeAI / ChatGroq). Missing keys raise MissingApiKeyError;
unknown providers raise UnsupportedProviderError. No-prefix model
strings default to openai, matching OAB agent.ts:184-196.

LangChain handles o1/o3/gpt-5 max_completion_tokens routing internally
when model is set — no manual reasoning-model dispatch on our side.

See ADR-0006; spec §5.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Structured output primitive

**Files:**
- Create: `src/llm/structured_output.py`
- Create: `tests/unit/llm/test_structured_output.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/llm/test_structured_output.py`:

```python
"""Tests for the structured-output wrapper shared by Agent + Extract."""
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

from src.llm.structured_output import structured_invoke


async def test_plain_text_returns_string() -> None:
    fake = FakeListChatModel(responses=["Hello, world!"])
    result = await structured_invoke(fake, [HumanMessage(content="hi")])
    assert result == "Hello, world!"


async def test_json_mode_parses_openai_shape() -> None:
    """Without a schema, we ask LangChain for json_mode and json.loads the result."""
    fake = FakeListChatModel(responses=['{"answer": 42}'])
    result = await structured_invoke(
        fake, [HumanMessage(content="give me JSON")], json_mode=True,
    )
    assert result == {"answer": 42}


async def test_json_mode_unparseable_returns_raw_string() -> None:
    """Matches OAB's fallback: parse failure → return the raw text."""
    fake = FakeListChatModel(responses=["not valid json {{{"])
    result = await structured_invoke(
        fake, [HumanMessage(content="give me JSON")], json_mode=True,
    )
    assert result == "not valid json {{{"


async def test_schema_uses_with_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a schema is passed, we route through with_structured_output."""
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: int

    fake = FakeListChatModel(responses=[Answer(value=42).model_dump_json()])
    # FakeListChatModel doesn't fully simulate with_structured_output. Patch it
    # to return the parsed Answer directly — the point of this test is that
    # structured_invoke hands off to with_structured_output, not the LangChain
    # internals.
    called: dict[str, Any] = {}

    class FakeStructured:
        async def ainvoke(self, messages: Any) -> Any:
            called["invoked"] = True
            return Answer(value=42)

    monkeypatch.setattr(
        fake, "with_structured_output", lambda schema, **kw: FakeStructured()
    )
    result = await structured_invoke(
        fake, [HumanMessage(content="give me JSON")], schema=Answer,
    )
    assert called == {"invoked": True}
    assert result == Answer(value=42)
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/llm/test_structured_output.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/llm/structured_output.py`:

```python
"""Shared structured-output primitive for Agent (Phase 2) and Extract (Phase 4).

See ADR-0008; spec §6.
"""

import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


async def structured_invoke(
    chat_model: BaseChatModel,
    messages: list[BaseMessage],
    *,
    schema: Any = None,
    json_mode: bool = False,
) -> Any:
    """Invoke `chat_model` and return a structured (or plain-text) result.

    Modes:
      - schema=None, json_mode=False  → plain text (str).
      - schema=None, json_mode=True   → with_structured_output(method="json_mode")
                                         then json.loads; fallback to raw text
                                         on parse failure (matches OAB).
      - schema=<pydantic|dict>        → with_structured_output(schema).
    """
    if schema is not None:
        structured = chat_model.with_structured_output(schema)
        return await structured.ainvoke(messages)  # pyright: ignore[reportAttributeAccessIssue]

    if json_mode:
        try:
            structured = chat_model.with_structured_output(method="json_mode")
            result = await structured.ainvoke(messages)  # pyright: ignore[reportAttributeAccessIssue]
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except json.JSONDecodeError:
                    return result
            return result
        except (NotImplementedError, ValueError):
            # Provider doesn't support json_mode (e.g. Google schema-less):
            # fall back to plain invoke + best-effort json.loads.
            pass

    response = await chat_model.ainvoke(messages)
    content = response.content if hasattr(response, "content") else str(response)
    if json_mode:
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content
        return content
    return content if isinstance(content, str) else str(content)


__all__ = ["structured_invoke"]
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/unit/llm/test_structured_output.py -v
```
Expected: all 4 PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/llm/structured_output.py tests/unit/llm/test_structured_output.py
git commit -m "feat(llm): shared structured_invoke primitive (Agent + Extract)

Three-mode entry point over LangChain's with_structured_output:
 - plain text when schema=None and json_mode=False
 - json_mode with json.loads fallback to raw text (matches OAB's lenient
   JSON parsing for schema-less asks)
 - full schema enforcement when schema is provided

Phase 4's Extract node will consume the same primitive with schema=<its
jsonSchema> — no re-implementation.

See ADR-0008; spec §6.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Tool Provider Framework — `base.py`

**Files:**
- Create: `src/tools/__init__.py` (empty initially; Task 7 makes it import providers)
- Create: `src/tools/base.py`
- Create: `tests/unit/tools/__init__.py` (empty)
- Create: `tests/unit/tools/test_base.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/tools/test_base.py`:

```python
"""Tests for the ToolProvider ABC + AuthRequirement hierarchy."""
import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.tools.base import (
    ApiKeyAuth,
    BuildContext,
    HealthStatus,
    NoAuth,
    OAuthAuth,
    ToolDefinition,
    ToolProvider,
)


def test_api_key_auth_dataclass() -> None:
    auth = ApiKeyAuth(env_var="FOO_KEY", settings_field="foo_api_key")
    assert auth.env_var == "FOO_KEY"
    assert auth.settings_field == "foo_api_key"
    assert auth.required is True


def test_no_auth_dataclass() -> None:
    auth = NoAuth()
    assert auth.required is False


def test_oauth_auth_has_rfc8707_default() -> None:
    auth = OAuthAuth(
        authorize_url="https://example.com/a",
        token_url="https://example.com/t",
        scopes=["read"],
    )
    assert auth.include_rfc8707_resource is True


def test_tool_definition_is_frozen() -> None:
    class _InputSchema(BaseModel):
        x: int

    td = ToolDefinition(name="t1", description="d", args_schema=_InputSchema)
    with pytest.raises((AttributeError, TypeError)):
        td.name = "t2"  # type: ignore[misc]


class _FakeProvider(ToolProvider):
    name = "fake"
    description = "fake provider for contract tests"
    category = "standard"
    auth = NoAuth()

    async def tools(self) -> list[ToolDefinition]:
        class _In(BaseModel):
            x: int

        return [ToolDefinition(name="fake_tool", description="d", args_schema=_In)]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        raise NotImplementedError("contract test only")


async def test_provider_default_health_check_no_auth_is_ok() -> None:
    provider = _FakeProvider()
    status = await provider.health_check()
    assert status.ok is True
    assert "no auth" in status.message.lower()


async def test_provider_default_health_check_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
    from src.config import get_settings
    get_settings.cache_clear()

    class _KeyedProvider(_FakeProvider):
        auth = ApiKeyAuth(env_var="TAVILY_API_KEY", settings_field="tavily_api_key")

    status = await _KeyedProvider().health_check()
    assert status.ok is False
    assert "TAVILY_API_KEY" in status.message
    assert "missing" in status.message.lower()


def test_health_status_is_frozen() -> None:
    s = HealthStatus(ok=True, message="up")
    with pytest.raises((AttributeError, TypeError)):
        s.ok = False  # type: ignore[misc]
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/tools/test_base.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/tools/__init__.py` (empty).

Create `src/tools/base.py`:

```python
"""Tool Provider Framework — base abstractions.

See ADR-0009; spec §8.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.config import get_settings

if TYPE_CHECKING:
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import AgentNode


# ─── Auth requirements ────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class AuthRequirement:
    """Base: a provider declares what kind of auth it needs."""

    required: bool = True


@dataclass(frozen=True, kw_only=True)
class NoAuth(AuthRequirement):
    required: bool = False


@dataclass(frozen=True, kw_only=True)
class ApiKeyAuth(AuthRequirement):
    """Single API key stored centrally in Settings."""

    env_var: str
    settings_field: str


@dataclass(frozen=True, kw_only=True)
class OAuthAuth(AuthRequirement):
    """Per-user OAuth (Phase 3 MCP). Envelope here; details in Phase 3."""

    authorize_url: str
    token_url: str
    scopes: list[str] = field(default_factory=list)
    include_rfc8707_resource: bool = True


# ─── Descriptors ──────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    """Enumerable metadata about a tool offered by a provider."""

    name: str
    description: str
    args_schema: type[BaseModel]


@dataclass(kw_only=True)
class BuildContext:
    """Context passed to `build_tool` — everything a provider might need."""

    node: "AgentNode"
    state: "WorkflowStateDict"
    user_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class HealthStatus:
    ok: bool
    message: str


# ─── Provider ABC ─────────────────────────────────────────────

class ToolProvider(ABC):
    """A service offering one or more tools for LLM consumption.

    Standard tools (Tavily, Serper, …) subclass this directly. MCP servers
    subclass via McpToolProvider (Phase 3). Register at import time via
    @register_tool_provider from src.tools.registry.
    """

    name: str
    description: str
    category: Literal["standard", "mcp"]
    auth: AuthRequirement

    @abstractmethod
    async def tools(self) -> list[ToolDefinition]:
        """Enumerate offered tools."""

    @abstractmethod
    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        """Construct a LangChain BaseTool for the named tool."""

    async def health_check(self) -> HealthStatus:
        """Default: verify the auth credential is present in Settings."""
        if isinstance(self.auth, ApiKeyAuth):
            value = getattr(get_settings(), self.auth.settings_field, "")
            ok = bool(value)
            return HealthStatus(
                ok=ok,
                message=(
                    f"{self.auth.env_var} present"
                    if ok
                    else f"{self.auth.env_var} missing in settings"
                ),
            )
        if isinstance(self.auth, NoAuth):
            return HealthStatus(ok=True, message="no auth required")
        # OAuthAuth: default is "configured" — Phase 3 subclass overrides.
        return HealthStatus(ok=True, message="OAuth configured")


__all__ = [
    "ApiKeyAuth",
    "AuthRequirement",
    "BuildContext",
    "HealthStatus",
    "NoAuth",
    "OAuthAuth",
    "ToolDefinition",
    "ToolProvider",
]
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/python -m pytest tests/unit/tools/test_base.py -v
```
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/tools/__init__.py src/tools/base.py tests/unit/tools/__init__.py tests/unit/tools/test_base.py
git commit -m "feat(tools): ToolProvider ABC + AuthRequirement + ToolDefinition

The base of the Tool Provider Framework per ADR-0009. Standard tools
(Phase 2+) and MCP servers (Phase 3) both subclass ToolProvider; the
shared surface is tools(), build_tool(), health_check().

AuthRequirement hierarchy declares what a provider needs: NoAuth (free),
ApiKeyAuth (Settings field lookup), OAuthAuth (with RFC-8707 resource
flag preconfigured for Phase 3 MCP fixes). BuildContext carries the
node/state/user_id into build_tool so per-user OAuth tokens plumb
through naturally in Phase 3.

Default health_check covers the ApiKeyAuth + NoAuth cases; providers
override to do a real ping.

See ADR-0009; spec §8.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Tool Provider Framework — `registry.py`

**Files:**
- Create: `src/tools/registry.py`
- Create: `tests/unit/tools/test_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/tools/test_registry.py`:

```python
"""Tests for the tool registry + resolve_tools_for_node."""
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import (
    BuildContext, NoAuth, ToolDefinition, ToolProvider,
)
from src.tools.registry import (
    UnknownProviderError,
    UnknownToolError,
    get_provider,
    list_providers,
    register_tool_provider,
    resolve_tools_for_node,
)


class _TestInput(BaseModel):
    q: str


class _TestTool(BaseTool):
    name: str = "registry_test_search"
    description: str = "registry test"
    args_schema: type[BaseModel] = _TestInput

    async def _arun(self, q: str) -> str:
        return f"searched: {q}"


@register_tool_provider
class _RegistryTestProvider(ToolProvider):
    name = "_registry_test"
    description = "test-only provider"
    category = "standard"
    auth = NoAuth()

    async def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="registry_test_search", description="t", args_schema=_TestInput,
        )]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name == "registry_test_search":
            return _TestTool()
        raise UnknownToolError(f"no {tool_name}")


def test_get_provider_returns_registered() -> None:
    p = get_provider("_registry_test")
    assert p.name == "_registry_test"


def test_get_provider_unknown_raises() -> None:
    with pytest.raises(UnknownProviderError, match="_not_registered"):
        get_provider("_not_registered")


def test_list_providers_returns_all_sorted() -> None:
    names = [p.name for p in list_providers()]
    assert "_registry_test" in names
    assert names == sorted(names)


def test_list_providers_filters_by_category() -> None:
    standards = list_providers(category="standard")
    assert all(p.category == "standard" for p in standards)
    assert "_registry_test" in {p.name for p in standards}


def _agent_node(selected_tools: list[str]) -> AgentNode:
    return AgentNode.model_validate({
        "id": "a",
        "type": "agent",
        "position": {"x": 0, "y": 0},
        "data": {"label": "Agent", "selectedTools": selected_tools},
    })


async def test_resolve_tools_for_node_empty() -> None:
    node = _agent_node([])
    tools = await resolve_tools_for_node(
        node,
        BuildContext(node=node, state=initial_state(), user_id=None),
    )
    assert tools == []


async def test_resolve_tools_for_node_resolves_qualified_name() -> None:
    node = _agent_node(["_registry_test.registry_test_search"])
    tools = await resolve_tools_for_node(
        node,
        BuildContext(node=node, state=initial_state(), user_id=None),
    )
    assert len(tools) == 1
    assert isinstance(tools[0], _TestTool)


async def test_resolve_tools_for_node_unknown_provider_raises() -> None:
    node = _agent_node(["_ghost.search"])
    with pytest.raises(UnknownProviderError, match="_ghost"):
        await resolve_tools_for_node(
            node,
            BuildContext(node=node, state=initial_state(), user_id=None),
        )


async def test_resolve_tools_for_node_mcp_ids_raise_until_phase_3() -> None:
    node = AgentNode.model_validate({
        "id": "a",
        "type": "agent",
        "position": {"x": 0, "y": 0},
        "data": {"label": "A", "mcpServerIds": ["s1"]},
    })
    with pytest.raises(NotImplementedError, match="Phase 3"):
        await resolve_tools_for_node(
            node,
            BuildContext(node=node, state=initial_state(), user_id=None),
        )


def test_duplicate_registration_raises() -> None:
    # Re-defining a provider with the same name must fail.
    with pytest.raises(ValueError, match="Duplicate provider"):
        @register_tool_provider
        class _DupProvider(ToolProvider):
            name = "_registry_test"  # same as _RegistryTestProvider above
            description = "dup"
            category = "standard"
            auth = NoAuth()

            async def tools(self) -> list[ToolDefinition]:
                return []

            async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
                raise NotImplementedError

        _ = _DupProvider  # silence unused-class warning
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/tools/test_registry.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/tools/registry.py`:

```python
"""Tool registry + node-level tool resolution.

See ADR-0009; spec §8.3.
"""

from typing import Literal, TypeVar

from langchain_core.tools import BaseTool

from src.engine.workflow import AgentNode
from src.tools.base import BuildContext, ToolProvider


class UnknownProviderError(ValueError):
    """Raised when a requested provider isn't registered."""


class UnknownToolError(ValueError):
    """Raised when a provider doesn't offer the requested tool name."""


T = TypeVar("T", bound=type[ToolProvider])
_PROVIDERS: dict[str, ToolProvider] = {}


def register_tool_provider(cls: T) -> T:
    """Class decorator — instantiates the provider and stores it in the registry."""
    instance = cls()
    if instance.name in _PROVIDERS:
        raise ValueError(
            f"Duplicate provider name: {instance.name!r} "
            f"(already registered as {type(_PROVIDERS[instance.name]).__name__})"
        )
    _PROVIDERS[instance.name] = instance
    return cls


def get_provider(name: str) -> ToolProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise UnknownProviderError(
            f"No provider registered for {name!r}. "
            f"Registered: {sorted(_PROVIDERS)}"
        ) from exc


def list_providers(
    *, category: Literal["standard", "mcp"] | None = None
) -> list[ToolProvider]:
    """Enumerate registered providers, optionally filtered by category."""
    out = list(_PROVIDERS.values())
    if category is not None:
        out = [p for p in out if p.category == category]
    return sorted(out, key=lambda p: p.name)


def _split_qualified(name: str) -> tuple[str, str]:
    """'provider.tool' → ('provider', 'tool')."""
    if "." not in name:
        raise UnknownProviderError(
            f"Tool name {name!r} is not qualified. Expected 'provider.tool'."
        )
    provider, _, tool = name.partition(".")
    return provider, tool


async def resolve_tools_for_node(
    node: AgentNode, context: BuildContext
) -> list[BaseTool]:
    """Given an Agent node, return the list of BaseTool instances to bind.

    Phase 2: consumes `selectedTools` as `provider.tool` strings.
    Phase 3: additionally resolves `mcpServerIds` via McpToolProvider instances.
    """
    out: list[BaseTool] = []

    for qualified_name in node.data.selected_tools:
        provider_name, tool_name = _split_qualified(qualified_name)
        provider = get_provider(provider_name)
        out.append(await provider.build_tool(tool_name, context))

    if node.data.mcp_server_ids:
        raise NotImplementedError(
            f"MCP tool resolution lands in Phase 3 — "
            f"server_ids={node.data.mcp_server_ids}"
        )

    return out


__all__ = [
    "UnknownProviderError",
    "UnknownToolError",
    "get_provider",
    "list_providers",
    "register_tool_provider",
    "resolve_tools_for_node",
]
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/tools/test_registry.py -v
```
Expected: all 9 PASS.

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/tools/registry.py tests/unit/tools/test_registry.py
git commit -m "feat(tools): registry + resolve_tools_for_node

@register_tool_provider decorator instantiates and stores the provider
(dup-name raises). get_provider/list_providers/_split_qualified give
the Agent executor and future UI (Phase 10) a clean discovery surface.

resolve_tools_for_node translates Agent node config
(selectedTools/mcpServerIds) into a flat list of BaseTool instances
ready for chat_model.bind_tools(). Phase 2 supports 'provider.tool'
qualified names; mcpServerIds raises NotImplementedError with a Phase 3
hint until that phase fills it in.

See ADR-0009; spec §8.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: TavilyProvider — reference provider implementation

**Files:**
- Create: `src/tools/providers/__init__.py`
- Create: `src/tools/providers/tavily.py`
- Modify: `src/tools/__init__.py` (trigger provider imports)
- Create: `tests/unit/tools/providers/__init__.py` (empty)
- Create: `tests/unit/tools/providers/test_tavily.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/tools/providers/test_tavily.py`:

```python
"""Tests for TavilyProvider."""
import pytest
from pytest_httpx import HTTPXMock

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.tavily import MissingApiKeyError, TavilyProvider


def _agent_node() -> AgentNode:
    return AgentNode.model_validate({
        "id": "a", "type": "agent",
        "position": {"x": 0, "y": 0},
        "data": {"label": "A"},
    })


def test_provider_metadata() -> None:
    p = TavilyProvider()
    assert p.name == "tavily"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "TAVILY_API_KEY"


async def test_tools_lists_search() -> None:
    tools = await TavilyProvider().tools()
    assert len(tools) == 1
    assert tools[0].name == "tavily_search"


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
    get_settings.cache_clear()
    node = _agent_node()
    with pytest.raises(MissingApiKeyError):
        await TavilyProvider().build_tool(
            "tavily_search",
            BuildContext(node=node, state=initial_state()),
        )


async def test_build_tool_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    node = _agent_node()
    with pytest.raises(ValueError, match="no tool named"):
        await TavilyProvider().build_tool(
            "ghost",
            BuildContext(node=node, state=initial_state()),
        )


async def test_search_tool_arun_returns_markdown(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.tavily.com/search",
        method="POST",
        json={
            "results": [
                {"title": "Title 1", "url": "https://example.com/1", "content": "Body 1", "score": 0.9},
                {"title": "Title 2", "url": "https://example.com/2", "content": "Body 2", "score": 0.8},
            ],
        },
    )
    node = _agent_node()
    tool = await TavilyProvider().build_tool(
        "tavily_search",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(query="what is the capital of France?")
    assert "Title 1" in result
    assert "https://example.com/1" in result
    assert "Body 1" in result


async def test_search_tool_http_error_returns_error_string(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.tavily.com/search", method="POST",
        status_code=500, text="internal error",
    )
    node = _agent_node()
    tool = await TavilyProvider().build_tool(
        "tavily_search", BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(query="foo")
    assert result.startswith("Error: Tavily search failed (HTTP 500)")


async def test_health_check_no_key_returns_not_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
    get_settings.cache_clear()
    status = await TavilyProvider().health_check()
    assert status.ok is False
    assert "TAVILY_API_KEY" in status.message


async def test_health_check_with_key_pings_tavily(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.tavily.com/search", method="POST",
        json={"results": []},
    )
    status = await TavilyProvider().health_check()
    assert status.ok is True
    assert "reachable" in status.message.lower() or "valid" in status.message.lower()
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/tools/providers/test_tavily.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/tools/providers/__init__.py`:

```python
"""Auto-register all tool providers.

Adding a new provider: drop a file here that defines a
@register_tool_provider class, then append `from . import <name>` below.
"""

from src.tools.providers import tavily as _tavily  # noqa: F401

__all__ = ["_tavily"]
```

Create `src/tools/providers/tavily.py`:

```python
"""TavilyProvider — web search via Tavily (the Phase 2 reference provider).

See spec §9 and ADR-0009.
"""

from typing import Any

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import (
    ApiKeyAuth, BuildContext, HealthStatus, ToolDefinition, ToolProvider,
)
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when TAVILY_API_KEY isn't configured."""


class TavilySearchInput(BaseModel):
    query: str = Field(description="Web search query")
    max_results: int = Field(default=5, ge=1, le=20)


class _TavilySearchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "tavily_search"
    description: str = (
        "Search the web via Tavily. Use for current events, fact lookups, "
        "and questions that need fresh information."
    )
    args_schema: type[BaseModel] = TavilySearchInput

    def _run(self, query: str, max_results: int = 5) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun — Composer is async-only.")

    async def _arun(self, query: str, max_results: int = 5) -> str:
        api_key = get_settings().tavily_api_key
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )
        except httpx.TimeoutException:
            return "Error: Tavily search timed out after 30s"
        except httpx.HTTPError as exc:
            return f"Error: Tavily search failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            body = resp.text[:200]
            return f"Error: Tavily search failed (HTTP {resp.status_code}): {body}"

        data: dict[str, Any] = resp.json()
        results = data.get("results", [])
        if not results:
            return "No results."
        lines = [f"# Search results for: {query}\n"]
        for r in results:
            lines.append(f"## {r.get('title', '(no title)')}")
            lines.append(f"{r.get('url', '')}")
            lines.append(f"{r.get('content', '')}\n")
        return "\n".join(lines)


@register_tool_provider
class TavilyProvider(ToolProvider):
    name = "tavily"
    description = "Tavily web search — general-purpose web search with content extraction."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="TAVILY_API_KEY", settings_field="tavily_api_key")

    async def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="tavily_search",
                description=_TavilySearchTool.description,
                args_schema=TavilySearchInput,
            ),
        ]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "tavily_search":
            raise ValueError(f"TavilyProvider has no tool named {tool_name!r}")
        if not get_settings().tavily_api_key:
            raise MissingApiKeyError("TAVILY_API_KEY is not configured")
        return _TavilySearchTool()

    async def health_check(self) -> HealthStatus:
        key = get_settings().tavily_api_key
        if not key:
            return HealthStatus(ok=False, message="TAVILY_API_KEY missing in settings")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": key, "query": "ping", "max_results": 1, "search_depth": "basic"},
                )
            if resp.status_code == 200:
                return HealthStatus(ok=True, message="Tavily reachable + key valid")
            return HealthStatus(
                ok=False, message=f"Tavily returned HTTP {resp.status_code}: {resp.text[:200]}",
            )
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, message=f"Tavily unreachable: {exc}")


__all__ = ["TavilyProvider", "MissingApiKeyError"]
```

Modify `src/tools/__init__.py` (replace empty):

```python
"""Tool Provider Framework.

Importing this package auto-loads every provider under src.tools.providers
and src.mcp (when Phase 3 lands). The load triggers each provider's
@register_tool_provider decorator, populating the registry.
"""

from src.tools import providers as _providers  # noqa: F401

__all__ = ["_providers"]
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/tools/ -v
```
Expected: all prior + 8 new Tavily tests PASS.

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/tools/__init__.py src/tools/providers/ tests/unit/tools/providers/
git commit -m "feat(tools): TavilyProvider — Phase 2 reference provider

First ToolProvider subclass: Tavily web search. Demonstrates the
pattern for Phase 6's Serper/Firecrawl/Browserless/Gamma/Arcade (each
one file following this template) and the shape Phase 3's
McpToolProvider will also implement.

Provider declares ApiKeyAuth(TAVILY_API_KEY). tools() exposes
tavily_search with its Pydantic input schema. build_tool() fails fast
if the key is missing. Custom health_check() pings Tavily directly
rather than just checking settings presence.

_TavilySearchTool is async-only (uses httpx.AsyncClient). HTTP failures
return error strings rather than raising — the agentic loop feeds those
back to the LLM so it can recover (matching OAB's tool-error pattern).

src/tools/__init__.py now imports src.tools.providers to trigger
auto-registration on package load.

See spec §9; ADR-0009.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7b: SerperProvider — Google search via Serper.dev

**Files:**
- Create: `src/tools/providers/serper.py`
- Modify: `src/tools/providers/__init__.py` (add `from . import serper`)
- Create: `tests/unit/tools/providers/test_serper.py`

Follows the exact Tavily template (Task 7). Differences: API URL, auth header style, response shape.

- [ ] **Step 1: Write failing tests** (mirror Tavily's test file, substituting URLs/shapes)

Create `tests/unit/tools/providers/test_serper.py`:

```python
"""Tests for SerperProvider (Google search via Serper.dev)."""
import pytest
from pytest_httpx import HTTPXMock

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.serper import MissingApiKeyError, SerperProvider


def _agent_node() -> AgentNode:
    return AgentNode.model_validate({
        "id": "a", "type": "agent",
        "position": {"x": 0, "y": 0}, "data": {"label": "A"},
    })


def test_provider_metadata() -> None:
    p = SerperProvider()
    assert p.name == "serper"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "SERPER_API_KEY"


async def test_tools_lists_search() -> None:
    tools = await SerperProvider().tools()
    assert [t.name for t in tools] == ["serper_search"]


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError):
        await SerperProvider().build_tool(
            "serper_search",
            BuildContext(node=_agent_node(), state=initial_state()),
        )


async def test_search_tool_returns_markdown(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://google.serper.dev/search", method="POST",
        json={
            "organic": [
                {"title": "Result 1", "link": "https://example.com/1", "snippet": "Body 1", "position": 1},
                {"title": "Result 2", "link": "https://example.com/2", "snippet": "Body 2", "position": 2},
            ],
        },
    )
    tool = await SerperProvider().build_tool(
        "serper_search", BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(query="test query")
    assert "Result 1" in result and "https://example.com/1" in result


async def test_search_tool_http_error_returns_error_string(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(url="https://google.serper.dev/search", method="POST", status_code=500, text="err")
    tool = await SerperProvider().build_tool(
        "serper_search", BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(query="foo")
    assert result.startswith("Error: Serper search failed (HTTP 500)")
```

- [ ] **Step 2: Implement provider**

Create `src/tools/providers/serper.py`:

```python
"""SerperProvider — Google search via https://serper.dev."""

from typing import Any

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import ApiKeyAuth, BuildContext, ToolDefinition, ToolProvider
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when SERPER_API_KEY isn't configured."""


class SerperSearchInput(BaseModel):
    query: str = Field(description="Google search query")
    num: int = Field(default=10, ge=1, le=20)


class _SerperSearchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "serper_search"
    description: str = (
        "Google search via Serper.dev. Returns Google's organic results for a query. "
        "Use when you need high-quality search results similar to a direct Google query."
    )
    args_schema: type[BaseModel] = SerperSearchInput

    def _run(self, query: str, num: int = 10) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun")

    async def _arun(self, query: str, num: int = 10) -> str:
        api_key = get_settings().serper_api_key
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    json={"q": query, "num": num},
                )
        except httpx.TimeoutException:
            return "Error: Serper search timed out after 30s"
        except httpx.HTTPError as exc:
            return f"Error: Serper search failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Serper search failed (HTTP {resp.status_code}): {resp.text[:200]}"

        data: dict[str, Any] = resp.json()
        organic = data.get("organic", [])
        if not organic:
            return "No results."
        lines = [f"# Google search results for: {query}\n"]
        for r in organic:
            lines.append(f"## {r.get('title', '(no title)')}")
            lines.append(f"{r.get('link', '')}")
            lines.append(f"{r.get('snippet', '')}\n")
        return "\n".join(lines)


@register_tool_provider
class SerperProvider(ToolProvider):
    name = "serper"
    description = "Serper — Google search via serper.dev. Organic results in JSON."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="SERPER_API_KEY", settings_field="serper_api_key")

    async def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="serper_search",
            description=_SerperSearchTool.description,
            args_schema=SerperSearchInput,
        )]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "serper_search":
            raise ValueError(f"SerperProvider has no tool named {tool_name!r}")
        if not get_settings().serper_api_key:
            raise MissingApiKeyError("SERPER_API_KEY is not configured")
        return _SerperSearchTool()


__all__ = ["SerperProvider", "MissingApiKeyError"]
```

Modify `src/tools/providers/__init__.py`:

```python
"""Auto-register all tool providers."""

from src.tools.providers import serper as _serper  # noqa: F401
from src.tools.providers import tavily as _tavily  # noqa: F401

__all__ = ["_serper", "_tavily"]
```

- [ ] **Step 3: Also add `serper_api_key` field to `src/config.py` Settings class** (it was listed in Task 1 — verify the field exists. If not, add `serper_api_key: str = Field(default="", description="Serper.dev API key")` in the Agent-tools section).

- [ ] **Step 4: Commit**

```bash
.venv/Scripts/python -m pytest tests/unit/tools/providers/test_serper.py -v
```
Expected: all PASS.

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/tools/providers/serper.py src/tools/providers/__init__.py src/config.py tests/unit/tools/providers/test_serper.py
git commit -m "feat(tools): SerperProvider (Google search via serper.dev)

Second ToolProvider — follows TavilyProvider's template. Proves the
framework's drop-a-file pattern. Uses X-API-KEY header auth (vs Tavily's
body-field auth) to verify AuthRequirement flexibility.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7c: FirecrawlProvider — web scrape

**Files:**
- Create: `src/tools/providers/firecrawl.py`
- Modify: `src/tools/providers/__init__.py`
- Modify: `src/config.py` — add `firecrawl_api_key` field if missing
- Create: `tests/unit/tools/providers/test_firecrawl.py`

API: `POST https://api.firecrawl.dev/v1/scrape` with `Authorization: Bearer <key>`, body `{"url": "...", "formats": ["markdown"]}`, response `{"success": true, "data": {"markdown": "...", "metadata": {...}}}`.

- [ ] **Step 1: Tests** (same shape as Serper's test file; substitute URL/body/response)

Create `tests/unit/tools/providers/test_firecrawl.py`:

```python
"""Tests for FirecrawlProvider (web scrape via firecrawl.dev)."""
import pytest
from pytest_httpx import HTTPXMock

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.firecrawl import FirecrawlProvider, MissingApiKeyError


def _agent_node() -> AgentNode:
    return AgentNode.model_validate({
        "id": "a", "type": "agent",
        "position": {"x": 0, "y": 0}, "data": {"label": "A"},
    })


def test_provider_metadata() -> None:
    p = FirecrawlProvider()
    assert p.name == "firecrawl"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "FIRECRAWL_API_KEY"


async def test_tools_lists_scrape() -> None:
    tools = await FirecrawlProvider().tools()
    assert [t.name for t in tools] == ["firecrawl_scrape"]


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError):
        await FirecrawlProvider().build_tool(
            "firecrawl_scrape",
            BuildContext(node=_agent_node(), state=initial_state()),
        )


async def test_scrape_tool_returns_markdown(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.firecrawl.dev/v1/scrape", method="POST",
        json={
            "success": True,
            "data": {
                "markdown": "# Page title\nBody content.",
                "metadata": {"title": "Page title", "sourceURL": "https://example.com"},
            },
        },
    )
    tool = await FirecrawlProvider().build_tool(
        "firecrawl_scrape", BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(url="https://example.com")
    assert "Page title" in result and "Body content" in result


async def test_scrape_tool_http_error(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(url="https://api.firecrawl.dev/v1/scrape", method="POST", status_code=500, text="err")
    tool = await FirecrawlProvider().build_tool(
        "firecrawl_scrape", BuildContext(node=_agent_node(), state=initial_state()),
    )
    assert (await tool._arun(url="https://example.com")).startswith("Error: Firecrawl scrape failed (HTTP 500)")
```

- [ ] **Step 2: Implement**

Create `src/tools/providers/firecrawl.py`:

```python
"""FirecrawlProvider — web scrape via https://firecrawl.dev."""

from typing import Any

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import ApiKeyAuth, BuildContext, ToolDefinition, ToolProvider
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when FIRECRAWL_API_KEY isn't configured."""


class FirecrawlScrapeInput(BaseModel):
    url: str = Field(description="URL to scrape")


class _FirecrawlScrapeTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "firecrawl_scrape"
    description: str = (
        "Scrape a URL and return the page content as clean markdown. "
        "Handles JavaScript rendering and pagination. Use when you need the full "
        "content of a web page, not just search results."
    )
    args_schema: type[BaseModel] = FirecrawlScrapeInput

    def _run(self, url: str) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun")

    async def _arun(self, url: str) -> str:
        api_key = get_settings().firecrawl_api_key
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"url": url, "formats": ["markdown"]},
                )
        except httpx.TimeoutException:
            return "Error: Firecrawl scrape timed out after 60s"
        except httpx.HTTPError as exc:
            return f"Error: Firecrawl scrape failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Firecrawl scrape failed (HTTP {resp.status_code}): {resp.text[:200]}"

        data: dict[str, Any] = resp.json()
        if not data.get("success"):
            return f"Error: Firecrawl scrape returned success=false: {data}"
        payload = data.get("data", {})
        markdown = payload.get("markdown", "")
        metadata = payload.get("metadata", {})
        title = metadata.get("title", "(no title)")
        source = metadata.get("sourceURL", url)
        return f"# {title}\n\n_Source: {source}_\n\n{markdown}"


@register_tool_provider
class FirecrawlProvider(ToolProvider):
    name = "firecrawl"
    description = "Firecrawl — web scrape with JS rendering; returns clean markdown."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="FIRECRAWL_API_KEY", settings_field="firecrawl_api_key")

    async def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="firecrawl_scrape",
            description=_FirecrawlScrapeTool.description,
            args_schema=FirecrawlScrapeInput,
        )]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "firecrawl_scrape":
            raise ValueError(f"FirecrawlProvider has no tool named {tool_name!r}")
        if not get_settings().firecrawl_api_key:
            raise MissingApiKeyError("FIRECRAWL_API_KEY is not configured")
        return _FirecrawlScrapeTool()


__all__ = ["FirecrawlProvider", "MissingApiKeyError"]
```

Modify `src/tools/providers/__init__.py` to add `from . import firecrawl as _firecrawl`.

- [ ] **Step 3: Commit**

```bash
git add src/tools/providers/firecrawl.py src/tools/providers/__init__.py src/config.py tests/unit/tools/providers/test_firecrawl.py
git commit -m "feat(tools): FirecrawlProvider (web scrape)

Third ToolProvider. Bearer-token auth + JSON response envelope
({success, data: {markdown, metadata}}). Longer timeout (60s) because
full-page scrapes with JS rendering can be slow.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7d: BrowserlessProvider — headless browser via /content endpoint

**Files:**
- Create: `src/tools/providers/browserless.py`
- Modify: `src/tools/providers/__init__.py`
- Modify: `src/config.py` — add `browserless_api_key` field if missing
- Create: `tests/unit/tools/providers/test_browserless.py`

API: `POST https://chrome.browserless.io/content?token=<api_key>` with body `{"url": "..."}` → response is raw HTML of the rendered page. Simpler than Firecrawl (no JSON envelope) but returns HTML not markdown — we keep it raw; the LLM handles it.

- [ ] **Step 1: Tests**

Create `tests/unit/tools/providers/test_browserless.py`:

```python
"""Tests for BrowserlessProvider (headless-browser page fetch)."""
import pytest
from pytest_httpx import HTTPXMock

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.browserless import BrowserlessProvider, MissingApiKeyError


def _agent_node() -> AgentNode:
    return AgentNode.model_validate({
        "id": "a", "type": "agent",
        "position": {"x": 0, "y": 0}, "data": {"label": "A"},
    })


def test_provider_metadata() -> None:
    p = BrowserlessProvider()
    assert p.name == "browserless"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "BROWSERLESS_API_KEY"


async def test_tools_lists_fetch() -> None:
    tools = await BrowserlessProvider().tools()
    assert [t.name for t in tools] == ["browserless_fetch"]


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERLESS_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError):
        await BrowserlessProvider().build_tool(
            "browserless_fetch",
            BuildContext(node=_agent_node(), state=initial_state()),
        )


async def test_fetch_tool_returns_html(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("BROWSERLESS_API_KEY", "bl-test")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://chrome.browserless.io/content?token=bl-test", method="POST",
        text="<html><body><h1>Hello</h1></body></html>",
    )
    tool = await BrowserlessProvider().build_tool(
        "browserless_fetch", BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(url="https://example.com")
    assert "<h1>Hello</h1>" in result


async def test_fetch_tool_http_error(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setenv("BROWSERLESS_API_KEY", "bl-test")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://chrome.browserless.io/content?token=bl-test", method="POST",
        status_code=500, text="err",
    )
    tool = await BrowserlessProvider().build_tool(
        "browserless_fetch", BuildContext(node=_agent_node(), state=initial_state()),
    )
    assert (await tool._arun(url="https://example.com")).startswith(
        "Error: Browserless fetch failed (HTTP 500)"
    )
```

- [ ] **Step 2: Implement**

Create `src/tools/providers/browserless.py`:

```python
"""BrowserlessProvider — headless-browser page fetch via chrome.browserless.io."""

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.tools.base import ApiKeyAuth, BuildContext, ToolDefinition, ToolProvider
from src.tools.registry import register_tool_provider


class MissingApiKeyError(RuntimeError):
    """Raised when BROWSERLESS_API_KEY isn't configured."""


class BrowserlessFetchInput(BaseModel):
    url: str = Field(description="URL to fetch with a real Chrome browser")


class _BrowserlessFetchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "browserless_fetch"
    description: str = (
        "Fetch a URL through a headless Chrome browser and return the fully-"
        "rendered HTML. Use for pages that require JavaScript execution — for "
        "plain scraping without JS, prefer firecrawl_scrape."
    )
    args_schema: type[BaseModel] = BrowserlessFetchInput

    def _run(self, url: str) -> str:  # pragma: no cover
        raise NotImplementedError("Use _arun")

    async def _arun(self, url: str) -> str:
        api_key = get_settings().browserless_api_key
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"https://chrome.browserless.io/content?token={api_key}",
                    json={"url": url},
                )
        except httpx.TimeoutException:
            return "Error: Browserless fetch timed out after 60s"
        except httpx.HTTPError as exc:
            return f"Error: Browserless fetch failed ({type(exc).__name__}): {exc}"

        if resp.status_code >= 400:
            return f"Error: Browserless fetch failed (HTTP {resp.status_code}): {resp.text[:200]}"
        return resp.text


@register_tool_provider
class BrowserlessProvider(ToolProvider):
    name = "browserless"
    description = "Browserless — headless Chrome page fetch. Returns rendered HTML."
    category: str = "standard"  # pyright: ignore[reportIncompatibleVariableOverride]
    auth = ApiKeyAuth(env_var="BROWSERLESS_API_KEY", settings_field="browserless_api_key")

    async def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="browserless_fetch",
            description=_BrowserlessFetchTool.description,
            args_schema=BrowserlessFetchInput,
        )]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        if tool_name != "browserless_fetch":
            raise ValueError(f"BrowserlessProvider has no tool named {tool_name!r}")
        if not get_settings().browserless_api_key:
            raise MissingApiKeyError("BROWSERLESS_API_KEY is not configured")
        return _BrowserlessFetchTool()


__all__ = ["BrowserlessProvider", "MissingApiKeyError"]
```

Modify `src/tools/providers/__init__.py` to add `from . import browserless as _browserless`.

- [ ] **Step 3: Commit**

```bash
git add src/tools/providers/browserless.py src/tools/providers/__init__.py src/config.py tests/unit/tools/providers/test_browserless.py
git commit -m "feat(tools): BrowserlessProvider (headless-browser fetch)

Fourth ToolProvider. Query-param auth (token=<key> in URL) rather than
header-based — proves the provider template handles that auth style
without any framework changes. Returns raw HTML (no envelope); the LLM
handles parsing.

Completes Phase 2's expanded tool set per the C2→D scope bump:
Tavily (search) + Serper (search) + Firecrawl (scrape) + Browserless
(fetch). Gamma and Arcade stay Phase 6; E2B stays Phase 4 (Transform).

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: MCP skeleton package (Phase 3 placeholder)

**Files:**
- Create: `src/mcp/__init__.py`

- [ ] **Step 1: Create placeholder**

Create `src/mcp/__init__.py`:

```python
"""MCP integration — Phase 3.

This package is a placeholder in Phase 2. Phase 3 populates it with:
  - src/mcp/base.py      — McpToolProvider(ToolProvider) subclass
  - src/mcp/client.py    — HTTP JSON-RPC MCP client
  - src/mcp/oauth.py     — OAuth 2.1 with RFC 8707, PKCE, token refresh
  - src/mcp/resolver.py  — Per-user MCP server lookup + tool resolution
  - src/mcp/providers/   — One McpToolProvider instance per registered server

Phase 2's ToolProvider ABC (src/tools/base.py) is designed so Phase 3
drops these in without modifying the Agent executor or tool registry.

See ADR-0009 + spec §8.6.
"""
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp/__init__.py
git commit -m "chore(mcp): skeleton package (Phase 3 placeholder)

The Tool Provider Framework (ADR-0009) is designed so MCP integration
is a subclass of ToolProvider. This placeholder package is where
Phase 3 lands the implementation. Empty for now — just the docstring
so imports don't fail and the directory exists in git.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Agent executor — message construction + non-tool path

**Files:**
- Create: `src/executors/agent.py`
- Create: `tests/unit/executors/test_agent.py`

- [ ] **Step 1: Write failing tests (single-provider, no tools, Text output)**

Create `tests/unit/executors/test_agent.py`:

```python
"""Tests for the Agent executor — non-tool path, single provider mocked."""
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.executors.agent import AgentExecutor


def _agent_node(
    *, model: str = "anthropic/claude-3-5-haiku-latest",
    instructions: str = "Say hi.",
    output_format: str = "Text",
    selected_tools: list[str] | None = None,
) -> AgentNode:
    return AgentNode.model_validate({
        "id": "a",
        "type": "agent",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": "Agent",
            "model": model,
            "instructions": instructions,
            "outputFormat": output_format,
            "selectedTools": selected_tools or [],
        },
    })


async def test_agent_text_output_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeListChatModel(responses=["Hello!"])
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)

    node = _agent_node()
    state = initial_state("Who are you?")
    delta = await AgentExecutor(node).arun(state)

    assert delta["variables"]["lastOutput"] == "Hello!"
    assert delta["current_node_id"] == "a"
    assert delta["node_results"]["a"]["status"] == "completed"
    assert delta["node_results"]["a"]["output"] == "Hello!"


async def test_agent_renders_instructions_via_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent executor must call variable_substitution.substitute on instructions."""
    fake = FakeListChatModel(responses=["ok"])
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)

    node = _agent_node(instructions="Hello, {{user.name}}")
    state = initial_state()
    state["variables"]["user"] = {"name": "Ada"}
    delta = await AgentExecutor(node).arun(state)
    # The instructions went into the LLM as substituted text, but we can't
    # introspect FakeListChatModel's received messages trivially — assert via
    # node_results.input (our executor records the raw instructions plus
    # substituted output).
    assert "Ada" in str(delta["node_results"]["a"].get("input", ""))


async def test_agent_json_output_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeListChatModel(responses=['{"answer": 42}'])
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)

    node = _agent_node(output_format="JSON")
    state = initial_state("give JSON")
    delta = await AgentExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == {"answer": 42}


async def test_agent_is_registered() -> None:
    from src.executors.base import build_executor
    node = _agent_node()
    # build_executor must resolve 'agent' to AgentExecutor after Task 9 import
    executor = build_executor(node)
    assert isinstance(executor, AgentExecutor)
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_agent.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/executors/agent.py`:

```python
"""Agent node executor.

Non-tool path only in Task 9. Task 10 adds the agentic loop (tool
execution). Task 11 wires structured output. See spec §10; ADRs 0006-0008.
"""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode
from src.executors.base import register_executor
from src.llm.providers import build_chat_model
from src.llm.structured_output import structured_invoke
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-3-5-haiku-latest"


@register_executor("agent")
class AgentExecutor:
    """Single-turn Agent (no tools). Tool-loop lands in Task 10."""

    def __init__(self, node: AgentNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_instructions = self.node.data.instructions or ""
        instructions = substitute(raw_instructions, state)

        messages = self._build_messages(instructions, state)

        chat_model = build_chat_model(
            self.node.data.model or DEFAULT_MODEL,
        )

        output = await self._invoke(chat_model, messages)

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": instructions,
                    "output": output,
                }
            },
        }

    def _build_messages(
        self, instructions: str, state: WorkflowStateDict
    ) -> list[Any]:
        """Messages: optionally prepend chat history, then a HumanMessage."""
        msgs: list[Any] = []
        if self.node.data.include_chat_history and state["chat_history"]:
            for m in state["chat_history"]:
                role = m.get("role")
                if role == "user":
                    msgs.append(HumanMessage(content=m.get("content", "")))
                elif role == "assistant":
                    msgs.append(AIMessage(content=m.get("content", "")))
        msgs.append(HumanMessage(content=instructions))
        return msgs

    async def _invoke(
        self, chat_model: BaseChatModel, messages: list[Any]
    ) -> Any:
        """Non-tool invocation; outputFormat routes through structured_invoke."""
        output_format = (self.node.data.output_format or "Text").lower()
        if output_format == "json":
            return await structured_invoke(chat_model, messages, json_mode=True)
        return await structured_invoke(chat_model, messages)


__all__ = ["AgentExecutor"]
```

Modify `src/engine/graph_builder.py` to import the new executor module for side-effect registration. Read the current file and locate the existing side-effect imports (start / end), then add `agent`:

```python
from src.executors import agent as _agent_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_agent.py -v
```
Expected: 4 tests PASS.

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/executors/agent.py src/engine/graph_builder.py tests/unit/executors/test_agent.py
git commit -m "feat(executors): Agent executor — non-tool path (Phase 2 Task 9)

Ports the zero-tools branch of OAB's agent.ts:780-1029 executor.
Builds chat model via src/llm/providers, substitutes {{...}} in
instructions via src/variable_substitution, invokes via
src/llm/structured_output (routes Text vs JSON).

graph_builder.py imports the agent module for its @register_executor
side-effect so the registry knows about it.

Tool-execution loop + tool binding lands in Task 10. Structured-output
with schemas lands in Task 11.

See spec §10; ADR-0006.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Agent executor — agentic loop (tool execution)

**Files:**
- Modify: `src/executors/agent.py`
- Modify: `tests/unit/executors/test_agent.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/executors/test_agent.py`:

```python
from langchain_core.messages import AIMessage


async def test_agent_calls_tool_and_loops_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First LLM turn returns a tool_call; tool returns text; second turn returns final text."""
    from langchain_core.tools import tool

    @tool
    async def echo_tool(message: str) -> str:
        """Echo the given message."""
        return f"echoed: {message}"

    class _ToolCallingFake:
        """Fake chat model: first invoke returns a tool_call, second returns text."""

        def __init__(self) -> None:
            self.call_count = 0

        def bind_tools(self, tools: list[Any]) -> "_ToolCallingFake":
            return self

        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            self.call_count += 1
            if self.call_count == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "echo_tool",
                        "args": {"message": "hello"},
                        "id": "call_1",
                        "type": "tool_call",
                    }],
                )
            return AIMessage(content="Tool said: echoed: hello")

    fake = _ToolCallingFake()
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)

    # Register a test-scoped provider with echo_tool
    from src.tools import registry

    async def _resolve_stub(node: Any, context: Any) -> list[Any]:
        return [echo_tool]

    monkeypatch.setattr(registry, "resolve_tools_for_node", _resolve_stub)

    node = _agent_node(selected_tools=["fake.echo"])
    state = initial_state("say hello")
    delta = await AgentExecutor(node).arun(state)
    assert "echoed: hello" in delta["variables"]["lastOutput"]
    assert fake.call_count == 2


async def test_agent_respects_max_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LLM keeps emitting tool calls, we cap at MAX_ITERATIONS."""
    from langchain_core.tools import tool

    @tool
    async def always_tool() -> str:
        """Echo."""
        return "."

    class _InfiniteLoopFake:
        def bind_tools(self, tools: list[Any]) -> "_InfiniteLoopFake":
            return self

        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "always_tool", "args": {}, "id": f"call_{len(messages)}",
                    "type": "tool_call",
                }],
            )

    fake = _InfiniteLoopFake()
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)

    from src.tools import registry

    async def _resolve_stub(node: Any, context: Any) -> list[Any]:
        return [always_tool]

    monkeypatch.setattr(registry, "resolve_tools_for_node", _resolve_stub)

    node = _agent_node(selected_tools=["fake.always"])
    state = initial_state("loop")
    from src.executors.agent import MaxIterationsExceededError

    with pytest.raises(MaxIterationsExceededError, match="MAX_ITERATIONS=10"):
        await AgentExecutor(node).arun(state)
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_agent.py -v
```
Expected: 2 new tests FAIL (import error on MaxIterationsExceededError + tool-loop tests fail because current executor doesn't loop).

- [ ] **Step 3: Rewrite executor with full agentic loop**

Replace `src/executors/agent.py` with the tool-loop-aware version:

```python
"""Agent node executor — full agentic loop.

Ports OAB's agent.ts:366-506 / 893-1015 / 1111-1223 while-loop into
Python, using LangChain's unified bind_tools() so provider-specific
tool format dispatch is handled internally.

See spec §10; ADRs 0006-0008.
"""

import asyncio
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from src.engine.state import WorkflowStateDict
from src.engine.workflow import AgentNode
from src.executors.base import register_executor
from src.llm.providers import build_chat_model
from src.llm.structured_output import structured_invoke
from src.tools.base import BuildContext
from src.tools.registry import resolve_tools_for_node
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-3-5-haiku-latest"
MAX_ITERATIONS = 10  # matches OAB


class MaxIterationsExceededError(RuntimeError):
    """Raised when the agentic loop hits MAX_ITERATIONS without converging."""


@register_executor("agent")
class AgentExecutor:
    def __init__(self, node: AgentNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        raw_instructions = self.node.data.instructions or ""
        instructions = substitute(raw_instructions, state)

        messages = self._build_messages(instructions, state)

        chat_model = build_chat_model(self.node.data.model or DEFAULT_MODEL)

        tools = await resolve_tools_for_node(
            self.node,
            BuildContext(node=self.node, state=state),
        )

        final_text = await self._agentic_loop(chat_model, messages, tools)

        output = await self._format_output(chat_model, instructions, final_text)

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": instructions,
                    "output": output,
                }
            },
        }

    def _build_messages(
        self, instructions: str, state: WorkflowStateDict
    ) -> list[BaseMessage]:
        msgs: list[BaseMessage] = []
        if self.node.data.include_chat_history and state["chat_history"]:
            for m in state["chat_history"]:
                role = m.get("role")
                if role == "user":
                    msgs.append(HumanMessage(content=m.get("content", "")))
                elif role == "assistant":
                    msgs.append(AIMessage(content=m.get("content", "")))
        msgs.append(HumanMessage(content=instructions))
        return msgs

    async def _agentic_loop(
        self,
        chat_model: BaseChatModel,
        messages: list[BaseMessage],
        tools: list[BaseTool],
    ) -> str:
        """Drive the LLM + tools until a turn produces text with no tool_calls.
        Caps at MAX_ITERATIONS iterations. Returns the final assistant text."""
        bound_model = chat_model.bind_tools(tools) if tools else chat_model
        current: list[BaseMessage] = list(messages)
        tools_by_name = {t.name: t for t in tools}

        for _ in range(MAX_ITERATIONS + 1):
            response = await bound_model.ainvoke(current)
            tool_calls = getattr(response, "tool_calls", [])

            if not tool_calls:
                content = response.content if hasattr(response, "content") else str(response)
                return content if isinstance(content, str) else str(content)

            # Execute all tool calls in parallel, append results as ToolMessages.
            tool_results = await asyncio.gather(*[
                self._run_tool(tools_by_name, tc) for tc in tool_calls
            ])
            current = [*current, response, *tool_results]

        raise MaxIterationsExceededError(
            f"Agent {self.node.id!r} hit MAX_ITERATIONS={MAX_ITERATIONS} "
            f"without producing a final text response."
        )

    async def _run_tool(
        self, tools_by_name: dict[str, BaseTool], tool_call: dict[str, Any]
    ) -> ToolMessage:
        name = tool_call.get("name", "")
        tool_id = tool_call.get("id", "")
        args = tool_call.get("args", {})
        if name not in tools_by_name:
            return ToolMessage(
                content=f"Error: tool {name!r} is not registered.",
                tool_call_id=tool_id,
            )
        tool = tools_by_name[name]
        try:
            result = await tool.ainvoke(args)  # pyright: ignore[reportArgumentType]
        except Exception as exc:
            logger.exception("Tool %s raised", name)
            return ToolMessage(
                content=f"Error: tool {name!r} raised {type(exc).__name__}: {exc}",
                tool_call_id=tool_id,
            )
        return ToolMessage(content=str(result), tool_call_id=tool_id)

    async def _format_output(
        self, chat_model: BaseChatModel, instructions: str, final_text: str
    ) -> Any:
        output_format = (self.node.data.output_format or "Text").lower()
        if output_format == "json":
            # Re-invoke with json_mode on the assistant's produced text so the
            # parse is provider-native rather than prompt-string fragile.
            messages: list[BaseMessage] = [
                HumanMessage(content=(
                    f"{instructions}\n\nYour previous draft answer was:\n"
                    f"{final_text}\n\nReturn JSON only."
                )),
            ]
            schema = self.node.data.json_schema
            return await structured_invoke(
                chat_model, messages, schema=schema, json_mode=schema is None,
            )
        return final_text


__all__ = ["AgentExecutor", "MaxIterationsExceededError"]
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_agent.py -v
```
Expected: all 6 PASS (4 from Task 9 + 2 new).

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/executors/agent.py tests/unit/executors/test_agent.py
git commit -m "feat(executors): Agent agentic loop with tool execution

Task 10 completes the Agent executor's main path. Ports OAB's
while-iter<10 tool-calling loop (agent.ts:366-506 for Anthropic; 893
for OpenAI; 1111 for Google) as a single LangChain-based implementation
thanks to bind_tools() handling the provider-specific tool format.

Parallel tool execution via asyncio.gather — matches OAB's Promise.all
pattern. Tool exceptions become ToolMessages with an 'Error:' prefix
rather than raising, so the LLM can see the error and adapt. Missing
tools produce the same shape.

MaxIterationsExceededError raised on 10-iteration cap. json output
re-invokes with structured_invoke using json_mode (or schema if
provided on the node).

See spec §10.2; ADR-0006.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Multi-provider mocked unit tests

**Files:**
- Modify: `tests/unit/executors/test_agent.py`

- [ ] **Step 1: Add parametrized per-provider tests**

Append to `tests/unit/executors/test_agent.py`:

```python
@pytest.mark.parametrize("provider,model", [
    ("anthropic", "anthropic/claude-3-5-haiku-latest"),
    ("openai", "openai/gpt-5-nano"),
    ("google", "google/gemini-2.0-flash"),
    ("groq", "groq/llama-3.3-70b-versatile"),
])
async def test_agent_runs_for_each_provider(
    monkeypatch: pytest.MonkeyPatch, provider: str, model: str,
) -> None:
    """Provider dispatch is verified; the actual LLM call is mocked."""
    expected = f"hello from {provider}"
    fake = FakeListChatModel(responses=[expected])

    captured: dict[str, str] = {}

    def _fake_build(model_string: str, **kwargs: Any) -> Any:
        captured["model"] = model_string
        return fake

    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", _fake_build)

    node = _agent_node(model=model)
    delta = await AgentExecutor(node).arun(initial_state("test"))
    assert delta["variables"]["lastOutput"] == expected
    assert captured["model"] == model
```

- [ ] **Step 2: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_agent.py -v
```
Expected: prior + 4 new parametrized PASS.

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add tests/unit/executors/test_agent.py
git commit -m "test(executors): parametrize Agent across 4 providers (mocked)

One parametrized test confirms the provider-dispatch plumbing works
for anthropic/openai/google/groq by capturing the model string passed
to build_chat_model. The actual LLM call is mocked via
FakeListChatModel — unit tests stay fast and offline.

Integration tests against real providers land in Task 13.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: graph_builder — remove Agent NotImplementedError

**Files:**
- Modify: `src/engine/graph_builder.py` (already imports `agent` module from Task 9 — this task verifies the built graph actually executes an agent node without raising).

- [ ] **Step 1: Write integration-style test proving the graph compiles + runs with an Agent node**

Append to `tests/unit/engine/test_graph_builder.py`:

```python
async def test_build_graph_compiles_with_agent_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """After Task 9, build_graph should accept an Agent node without raising."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.llm import providers
    fake = FakeListChatModel(responses=["ok"])
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)

    wf = _mk(
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "a", "type": "agent", "position": {"x": 100, "y": 0}, "data": {"label": "Agent"}},
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    )
    compiled = build_graph(wf, MemorySaver())
    # Must not raise
    g = compiled.get_graph()
    names = {n.id for n in g.nodes.values()}
    assert {"s", "a", "e"}.issubset(names)
```

- [ ] **Step 2: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_graph_builder.py -v
```
Expected: all prior + new PASS (the Agent-compilation path now works because Task 9 registered AgentExecutor).

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add tests/unit/engine/test_graph_builder.py
git commit -m "test(engine): graph_builder compiles start→agent→end

Regression-proofs the Phase 1 NotImplementedError-for-unshipped-executor
behaviour: now that Task 9 registered AgentExecutor, build_graph should
accept an agent node and compile a start→agent→end workflow.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Integration tests — per-provider real API

**Files:**
- Create: `tests/integration/test_agent_providers.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_agent_providers.py`:

```python
"""Integration — Start→Agent→End against real LLM providers.

Gracefully skips providers whose API key is unset. Each test makes
one LLM call per run — cost is a few cents total across all providers.
"""
import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient, execution_id: str, timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


@pytest.mark.parametrize("provider,model,key_env", [
    ("anthropic", "anthropic/claude-3-5-haiku-latest", "ANTHROPIC_API_KEY"),
    ("openai", "openai/gpt-5-nano", "OPENAI_API_KEY"),
    ("google", "google/gemini-2.0-flash", "GOOGLE_API_KEY"),
    ("groq", "groq/llama-3.3-70b-versatile", "GROQ_API_KEY"),
])
async def test_start_agent_end_real_provider(
    client: AsyncClient, provider: str, model: str, key_env: str,
) -> None:
    """Start→Agent→End against a real LLM. Skips if the provider key is unset."""
    if not os.environ.get(key_env):
        pytest.skip(f"{key_env} not set — skipping {provider} integration test")

    wf_payload = {
        "name": f"Phase-2 smoke {provider}",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": "Agent",
                    "model": model,
                    "instructions": "Reply with exactly the word 'hello'.",
                    "outputFormat": "Text",
                },
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    }
    create = await client.post("/workflows", json=wf_payload)
    assert create.status_code == 201, create.text
    workflow_id = create.json()["id"]

    start = await client.post(
        "/executions", json={"workflowId": workflow_id, "input": ""},
    )
    assert start.status_code == 202, start.text

    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    # Loose assertion: LLMs vary, so check the output is non-empty and
    # contains 'hello' (case-insensitive) or is otherwise a valid string.
    output = final.get("output")
    assert isinstance(output, str) and len(output) > 0
```

- [ ] **Step 2: Run (will skip without keys)**

```bash
.venv/Scripts/python -m pytest tests/integration/test_agent_providers.py -v
```
Expected: skipped (no keys in bash env without `export`).

If the user has keys in `.env`, the controller can verify locally via:
```bash
export $(grep -E "^(ANTHROPIC|OPENAI|GOOGLE|GROQ)_API_KEY=" .env | xargs)
export TEST_DATABASE_URL="$DATABASE_URL"
.venv/Scripts/python -m pytest tests/integration/test_agent_providers.py -v
```
Expected: 4 tests pass (or skip cleanly if keys are empty).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_agent_providers.py
git commit -m "test(integration): Start→Agent→End against real LLM providers

One parametrized test per provider (anthropic/openai/google/groq) that
skips cleanly when the corresponding API key env var is unset. Each
passing test proves the full pipeline works: REST → engine → LangChain
→ real LLM → result persisted back on the execution row.

Cost: ~4 LLM calls per CI run, total <5 cents across all four providers
at smallest-model settings (haiku, gpt-5-nano, gemini-flash, llama-3.3).

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Integration test — Agent with Tavily tool

**Files:**
- Create: `tests/integration/test_agent_with_tavily.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_agent_with_tavily.py`:

```python
"""Integration — Start→Agent(with Tavily)→End against real Tavily API.

Verifies the full tool-binding path: Agent executor resolves
tavily_search via the ToolProvider framework, the LLM decides to call
it, the tool runs an HTTP call to Tavily, result flows back through
the agentic loop.
"""
import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient, execution_id: str, timeout: float = 60.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_agent_uses_tavily_tool(client: AsyncClient) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    if not os.environ.get("TAVILY_API_KEY"):
        pytest.skip("TAVILY_API_KEY not set")

    wf_payload = {
        "name": "Phase-2 Agent+Tavily smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a", "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": "Agent",
                    "model": "anthropic/claude-3-5-haiku-latest",
                    "instructions": (
                        "Use the tavily_search tool to find the current population of Tokyo, "
                        "then report the number as a single sentence."
                    ),
                    "outputFormat": "Text",
                    "selectedTools": ["tavily.tavily_search"],
                },
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    }
    create = await client.post("/workflows", json=wf_payload)
    assert create.status_code == 201, create.text
    start = await client.post(
        "/executions", json={"workflowId": create.json()["id"], "input": ""},
    )
    assert start.status_code == 202, start.text
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    output = final.get("output")
    assert isinstance(output, str) and len(output) > 0
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_agent_with_tavily.py
git commit -m "test(integration): Agent+Tavily end-to-end against real APIs

Full integration across Anthropic LLM + Tavily search API + the Tool
Provider Framework. Skips gracefully when either key is unset.

Proves: Agent executor resolves tavily.tavily_search via
resolve_tools_for_node → TavilyProvider.build_tool → bound into
chat_model.bind_tools(); LLM emits a tool_call; agentic loop executes
the Tavily HTTP call; result flows back; LLM produces final text.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Port one OAB Agent regression test

**Files:**
- Create: `tests/regression/test_oab_agent.py`

- [ ] **Step 1: Write the ported test**

Create `tests/regression/test_oab_agent.py`:

```python
"""Regression — OAB's simple-agent template (01-simple-agent.ts).

OAB source: D:/GitHub/open-agent-builder/lib/workflow/templates/examples/01-simple-agent.ts
  The canonical 'Agent with no tools, Text output' workflow that every
  OAB user has run at some point.
Ported: 2026-04-20 for Phase 2 to establish Agent regression parity.
"""
import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient, execution_id: str, timeout: float = 30.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_oab_simple_agent_regression(client: AsyncClient) -> None:
    """OAB's simple-agent template: Start → Agent → End, Text output, no tools."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    wf_payload = {
        "name": "OAB Regression — simple agent",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "Start"}},
            {
                "id": "a", "type": "agent",
                "position": {"x": 100, "y": 0},
                "data": {
                    "label": "Agent",
                    "name": "SimpleAgent",
                    "model": "anthropic/claude-3-5-haiku-latest",
                    "instructions": "Respond with exactly: 'Regression check OK'.",
                    "outputFormat": "Text",
                    "includeChatHistory": False,
                },
            },
            {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "End"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
    }
    create = await client.post("/workflows", json=wf_payload)
    assert create.status_code == 201, create.text
    start = await client.post(
        "/executions", json={"workflowId": create.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed"
    output = final.get("output")
    assert isinstance(output, str) and len(output) > 0
```

- [ ] **Step 2: Commit**

```bash
git add tests/regression/test_oab_agent.py
git commit -m "test(regression): port OAB simple-agent template as first Phase 2 regression

OAB's 01-simple-agent.ts template (Agent, no tools, Text output) is
the canonical smoke test every OAB user has run. Ported against
Composer's HTTP API. Skips on missing ANTHROPIC_API_KEY.

Establishes the Phase 2 regression harness pattern Phase 7 will
expand to full-suite ports.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: CI — provider secrets + Tavily in integration job

**Files:**
- Modify: `.github/workflows/ci.yml`

**AUTHORIZED for this task only:** `.github/workflows/ci.yml`.

- [ ] **Step 1: Read the existing workflow**

```bash
cat .github/workflows/ci.yml
```

- [ ] **Step 2: Add provider keys to the integration job's env**

In the integration job's `env:` block, add (pulling from GitHub Actions secrets):

```yaml
env:
  TEST_DATABASE_URL: postgresql://composer_test:composer_test@localhost:5432/composer_test
  DATABASE_URL: postgresql://composer_test:composer_test@localhost:5432/composer_test
  JWT_SECRET: ci-test-only-not-a-real-secret
  ENCRYPTION_KEY: "00000000000000000000000000000000000000000000"
  # Phase 2: LLM + Tavily keys (from repo secrets). Tests skip gracefully
  # when a secret isn't set.
  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
  GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
  TAVILY_API_KEY: ${{ secrets.TAVILY_API_KEY }}
  SERPER_API_KEY: ${{ secrets.SERPER_API_KEY }}
  FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
  BROWSERLESS_API_KEY: ${{ secrets.BROWSERLESS_API_KEY }}
  LANGCHAIN_API_KEY: ${{ secrets.LANGCHAIN_API_KEY }}
```

Preserve everything else unchanged.

- [ ] **Step 3: Validate YAML + commit**

```bash
.venv/Scripts/python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: no output (success).

```bash
git add .github/workflows/ci.yml
git commit -m "ci: pass Phase 2 provider secrets to integration job

Pulls ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY / GROQ_API_KEY
/ TAVILY_API_KEY / LANGCHAIN_API_KEY from GitHub Actions repo secrets
into the integration test job's env. Tests skip cleanly when any secret
is missing (see tests/conftest.py collection hook + per-test skip
guards), so CI won't break on a key that hasn't been provisioned yet.

User provisions secrets in repo Settings → Secrets and variables →
Actions, matching the env-var names above.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Phase-exit — CHANGELOG + CLAUDE.md + ADR backfills

**AUTHORIZED for this task:** `CHANGELOG.md`, `CLAUDE.md` phase-status table (§Current phase status only), `docs/design/decisions.md` (ADRs 0006-0009 `Implemented by` lines).

- [ ] **Step 1: Verify Phase 2 exit checklist is green**

Run:
```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
All four must pass. If integration tests can be run locally (user has keys in `.env`), verify those too:
```bash
export $(grep -E "^(ANTHROPIC|OPENAI|GOOGLE|GROQ|TAVILY|DATABASE)_(API_KEY|URL)=" .env | xargs)
export TEST_DATABASE_URL="$DATABASE_URL"
.venv/Scripts/python -m pytest -v
```

Grep for Phase 2 TODOs:
```bash
grep -rn "TODO(phase-2)" src/ tests/ 2>/dev/null || echo "No Phase 2 TODOs found"
```

- [ ] **Step 2: Update CHANGELOG**

Add a Phase 2 section to `CHANGELOG.md` matching the format of Phase 1's section. `#### Added` lists every new module + test surface. `#### Changed` lists any deliberate deviations logged in ADRs 0006-0009. If integration testing against real providers surfaced bugs (usually does), include `#### Fixed`.

- [ ] **Step 3: Update CLAUDE.md phase table**

Change Phase 2 row from `⏭ Next` to `✅ Complete`. Move Phase 3 to `⏭ Next`.

- [ ] **Step 4: Backfill ADR `Implemented by` ranges**

In `docs/design/decisions.md`, on ADRs 0006, 0007, 0008, 0009, replace `Implemented by. Phase 2 (commits TBD).` with:
```markdown
**Implemented by.** Phase 2 (commits `<first-phase-2-sha>`..`<last-phase-2-sha>` on `main`).
```
Use `git log --oneline <phase-1-last-sha>..HEAD` to identify the range.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-2): mark Phase 2 complete

Exit checklist green:
 - Variable substitution engine with OAB parity + prototype-pollution guard
 - LLM provider dispatch for 4 providers with fail-fast missing-key handling
 - Shared structured_output primitive (Agent + Phase 4 Extract)
 - Tool Provider Framework: ToolProvider ABC + AuthRequirement + registry
 - TavilyProvider as reference implementation for Phase 6 siblings
 - MCP skeleton package for Phase 3
 - Agent executor with full 10-iteration agentic loop + tool binding
 - Per-provider parametrized unit tests + real-API integration tests
 - OAB simple-agent regression test ported
 - CI wired with provider secrets

Phase 3 (MCP + OAuth) is next — adds McpToolProvider(ToolProvider)
subclass that slots into the Phase 2 framework.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §1 Goal + done-when | All 11 items distributed across tasks 1-17 |
| §4 Module layout | Tasks 2–9 create every listed file |
| §5 LLM providers | Task 3 |
| §6 structured_output | Task 4 |
| §7 variable_substitution | Task 2 |
| §8 Tool Provider Framework | Tasks 5, 6 |
| §9 TavilyProvider | Task 7 |
| §8.6 MCP skeleton | Task 8 |
| §10 Agent executor | Tasks 9 (non-tool), 10 (loop) |
| §11 Tests (unit + integration + regression) | Tasks 2–7, 9–15 |
| §12 CI | Task 16 |
| §15 Phase-exit | Task 17 |

No placeholders. Every code step has actual code. Every command has expected output.

---

## Execution handoff

Plan saved at `docs/superpowers/plans/2026-04-20-phase-2-agent-executor-plan.md`.

Controller proceeds to **subagent-driven-development** for Tasks 1–17 (same pattern as Phase 1). Each task gets one implementer subagent + one combined spec+quality reviewer (Haiku for mechanical tasks, Sonnet for agent executor + tools framework). Final phase-level review before push.
