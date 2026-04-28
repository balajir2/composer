# Phase 4a — Linear Executors: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship five linear executors (`http`, `set-state`, `transform`, `data-transform`, `extract`) + two shared primitives (`substitute_in_value`, `_eval.py`), integration + regression tests, and phase-exit docs.

**Architecture.** Each executor is a single file under `src/executors/` implementing the `@register_executor(type)` + `arun(state) → delta` pattern from Phases 1–3. Two shared primitives: recursive `{{...}}` substitution (extends `src/variable_substitution.py`) and a `simpleeval` wrapper (`src/executors/_eval.py` — ADR-0012). Per-executor tests plus one multi-node integration test against real Neon + real Anthropic.

**Tech Stack:** Python 3.11/3.12, httpx, simpleeval, pytest + pytest-asyncio + pytest-httpx, Prisma Python, LangChain/Anthropic via Phase 2's `structured_invoke`.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-4a-linear-executors-design.md`](../specs/2026-04-21-phase-4a-linear-executors-design.md)
**ADR:** [ADR-0012](../../design/decisions.md#adr-0012-simpleeval-is-the-only-eval-primitive)

---

## Sequencing and discipline

10 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Integration tests (Task 8 + 9) run separately at phase-exit with `TEST_DATABASE_URL` pointing at Neon + `ANTHROPIC_API_KEY` set.

**Phase 1's node data shapes already declare the field names** (per ADR-0002). Executors consume those existing fields; schema amendments happen in the task that needs them (`http` adds `responsePath`, `data-transform` replaces its stub with proper fields, `extract` gains `input_text`). All three amendments preserve OAB's `camelCase` JSON aliases.

**⚠️ Forbidden files (all tasks except where explicitly noted):** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 10), `docs/design/*` (except Task 10 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`. Fix pyright strict errors with `# pyright: ignore[specific]` inline — never loosen `pyproject.toml`.

Commit footer (every commit):
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`. No feature branches.

---

## Task 1: Extend `variable_substitution.py` with `substitute_in_value`

**Files:**
- Modify: `src/variable_substitution.py`
- Modify: `tests/unit/test_variable_substitution.py`

- [ ] **Step 1: Append failing tests**

Read the current test file to see its style + imports. Append at the end:

```python
def test_substitute_in_value_string_leaf() -> None:
    from src.engine.state import initial_state
    from src.variable_substitution import substitute_in_value

    state = initial_state()
    state["variables"]["name"] = "Ada"
    assert substitute_in_value("hello {{name}}", state) == "hello Ada"


def test_substitute_in_value_dict_recurses() -> None:
    from src.engine.state import initial_state
    from src.variable_substitution import substitute_in_value

    state = initial_state()
    state["variables"]["api_key"] = "sk-abc"
    out = substitute_in_value(
        {"Authorization": "Bearer {{api_key}}", "X-User": "dev"}, state
    )
    assert out == {"Authorization": "Bearer sk-abc", "X-User": "dev"}


def test_substitute_in_value_list_recurses() -> None:
    from src.engine.state import initial_state
    from src.variable_substitution import substitute_in_value

    state = initial_state()
    state["variables"]["pets"] = "cats and dogs"
    assert substitute_in_value(["a", "{{pets}}", "b"], state) == ["a", "cats and dogs", "b"]


def test_substitute_in_value_nested_dict_list() -> None:
    from src.engine.state import initial_state
    from src.variable_substitution import substitute_in_value

    state = initial_state()
    state["variables"]["x"] = 42
    out = substitute_in_value(
        {"items": [{"label": "n={{x}}"}, "raw"]}, state
    )
    assert out == {"items": [{"label": "n=42"}, "raw"]}


def test_substitute_in_value_leaves_non_str_types() -> None:
    from src.engine.state import initial_state
    from src.variable_substitution import substitute_in_value

    state = initial_state()
    assert substitute_in_value(42, state) == 42
    assert substitute_in_value(True, state) is True
    assert substitute_in_value(None, state) is None
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/test_variable_substitution.py -v
```
Expected: 5 new tests fail with `ImportError: cannot import name 'substitute_in_value'`.

- [ ] **Step 3: Implement**

Read `src/variable_substitution.py`. Find the existing `substitute(template, state)` function. Append this new public function AFTER it (before `__all__`):

```python
def substitute_in_value(value: Any, state: WorkflowStateDict) -> Any:
    """Recursively apply {{...}} substitution over nested structures.

    Rules:
      - str: substitute via `substitute()`
      - dict: recurse into values; keys are left untouched
      - list: recurse into items
      - any other type (int, bool, None, etc.): returned unchanged
    """
    if isinstance(value, str):
        return substitute(value, state)
    if isinstance(value, dict):
        return {k: substitute_in_value(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_in_value(item, state) for item in value]
    return value
```

Add `substitute_in_value` to `__all__` (preserve sorted order).

If `from typing import Any` isn't already imported at the top of the file, add it.

- [ ] **Step 4: Run everything**

```bash
.venv/Scripts/python -m pytest tests/unit/test_variable_substitution.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 5 new + all existing pass; overall +5 over current Phase 3b baseline of 251.

- [ ] **Step 5: Commit**

```bash
git add src/variable_substitution.py tests/unit/test_variable_substitution.py
git commit -m "feat(substitution): substitute_in_value for recursive dict/list {{...}}

Consumers in Phase 4a:
  - http body + headers (dict values with templated leaves)
  - set-state value (arbitrary templated value)

Rules: str → substitute(); dict → recurse values (keys untouched);
list → recurse items; other types passthrough. No cycle detection —
Python's RecursionError catches self-referential dicts.

See Phase 4a spec §5.1.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `_eval.py` — simpleeval wrapper

**Files:**
- Create: `src/executors/_eval.py`
- Create: `tests/unit/executors/test_eval.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/executors/test_eval.py`:

```python
"""Tests for the simpleeval wrapper (ADR-0012)."""

import pytest

from src.engine.state import initial_state
from src.executors._eval import EvalError, evaluate


def test_evaluate_arithmetic() -> None:
    state = initial_state()
    assert evaluate("1 + 2 * 3", state) == 7


def test_evaluate_reads_variables() -> None:
    state = initial_state()
    state["variables"]["x"] = 10
    state["variables"]["y"] = 32
    assert evaluate("variables['x'] + variables['y']", state) == 42


def test_evaluate_last_output_shorthand() -> None:
    state = initial_state()
    state["variables"]["lastOutput"] = "hello"
    assert evaluate("lastOutput + ' world'", state) == "hello world"


def test_evaluate_extra_names_scope() -> None:
    state = initial_state()
    # Simulates data-transform per-item scope
    assert evaluate("item * 2", state, extra_names={"item": 21}) == 42


def test_evaluate_undefined_name_raises() -> None:
    state = initial_state()
    with pytest.raises(EvalError, match="ghost"):
        evaluate("ghost + 1", state)


def test_evaluate_builtins_blocked() -> None:
    state = initial_state()
    with pytest.raises(EvalError):
        evaluate("__import__('os').listdir()", state)


def test_evaluate_dunder_access_blocked() -> None:
    """simpleeval blocks attribute names starting with __."""
    state = initial_state()
    state["variables"]["s"] = "hello"
    with pytest.raises(EvalError):
        evaluate("variables['s'].__class__", state)


def test_evaluate_syntax_error_raises_eval_error() -> None:
    state = initial_state()
    with pytest.raises(EvalError):
        evaluate("1 +* 2", state)
```

Also ensure `tests/unit/executors/__init__.py` exists (it should from earlier phases; verify).

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_eval.py -v
```
Expected: `ImportError: cannot import name 'EvalError'` (or similar).

- [ ] **Step 3: Implement**

Create `src/executors/_eval.py`:

```python
"""simpleeval wrapper — the ONLY eval primitive in Composer (ADR-0012).

All user-supplied expression evaluation routes through `evaluate`. No
`eval()`, `exec()`, or `compile()` calls anywhere in src/. The scope
exposes a small, explicit whitelist of names; no builtins, no imports,
no dunder attribute access.

Scope:
  - variables       : dict — state["variables"]
  - lastOutput      : shorthand for variables["lastOutput"]
  - node_results    : dict — state["node_results"]
  - per-call extras : e.g., `item` for data-transform iteration
"""

from typing import Any

from simpleeval import (  # pyright: ignore[reportMissingTypeStubs]
    AttributeDoesNotExist,
    FunctionNotDefined,
    InvalidExpression,
    NameNotDefined,
    NumberTooHigh,
    SimpleEval,
)

from src.engine.state import WorkflowStateDict


class EvalError(RuntimeError):
    """Any simpleeval failure — invalid expression, undefined name, syntax, etc."""


def _build_evaluator(
    state: WorkflowStateDict,
    *,
    extra_names: dict[str, Any] | None = None,
) -> SimpleEval:
    """Return a SimpleEval bound to the per-node scope."""
    variables = dict(state.get("variables") or {})
    names: dict[str, Any] = {
        "variables": variables,
        "lastOutput": variables.get("lastOutput"),
        "node_results": dict(state.get("node_results") or {}),
    }
    if extra_names:
        names.update(extra_names)
    return SimpleEval(names=names)


def evaluate(
    expression: str,
    state: WorkflowStateDict,
    *,
    extra_names: dict[str, Any] | None = None,
) -> Any:
    """Evaluate a simpleeval expression over state.

    Raises EvalError on any failure (undefined name, syntax, blocked access).
    See ADR-0012.
    """
    try:
        return _build_evaluator(state, extra_names=extra_names).eval(expression)
    except (
        NameNotDefined,
        AttributeDoesNotExist,
        FunctionNotDefined,
        NumberTooHigh,
        InvalidExpression,
        SyntaxError,
    ) as exc:
        raise EvalError(
            f"simpleeval failed evaluating {expression!r}: {type(exc).__name__}: {exc}"
        ) from exc


__all__ = ["EvalError", "evaluate"]
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_eval.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 8 new pass; overall +8 (~264).

```bash
git add src/executors/_eval.py tests/unit/executors/test_eval.py
git commit -m "feat(executors): simpleeval wrapper — the single eval primitive

Per ADR-0012: every user-supplied expression evaluation routes through
src.executors._eval.evaluate(). No eval()/exec()/compile() anywhere in
src/.

Scope: variables, lastOutput, node_results, plus per-call extra_names
(e.g., item for data-transform). No builtins, no imports, no dunder
attribute access (simpleeval's default).

EvalError wraps all the simpleeval exception subtypes (NameNotDefined,
AttributeDoesNotExist, SyntaxError, etc.) so callers catch one thing.

Consumers: transform (Task 5), data-transform (Task 6), if-else and
while in Phase 4b.

See Phase 4a spec §5.2, ADR-0012.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `http` executor + sentinel test move

**Files:**
- Modify: `src/engine/workflow.py` (add `response_path` to HttpNodeData)
- Create: `src/executors/http.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_http_executor.py`
- Modify: `tests/unit/executors/test_registry.py` (sentinel: http → user-approval)
- Modify: `tests/unit/engine/test_graph_builder.py` (sentinel: http → user-approval)
- Modify: `tests/unit/engine/test_langgraph_executor.py` (sentinel: http → user-approval)

- [ ] **Step 1: Amend `HttpNodeData` in `src/engine/workflow.py`**

Read the file. Find:

```python
class HttpNodeData(BaseNodeData):
    http_url: str | None = Field(default=None, alias="httpUrl")
    http_method: str | None = Field(default=None, alias="httpMethod")
    http_headers: dict[str, str] = Field(default_factory=dict, alias="httpHeaders")
    http_body: Any | None = Field(default=None, alias="httpBody")
```

Add one field:

```python
class HttpNodeData(BaseNodeData):
    http_url: str | None = Field(default=None, alias="httpUrl")
    http_method: str | None = Field(default=None, alias="httpMethod")
    http_headers: dict[str, str] = Field(default_factory=dict, alias="httpHeaders")
    http_body: Any | None = Field(default=None, alias="httpBody")
    response_path: str | None = Field(default=None, alias="responsePath")
```

- [ ] **Step 2: Write failing tests**

Create `tests/unit/executors/test_http_executor.py`:

```python
"""Tests for the http executor."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.context import _current_db  # pyright: ignore[reportPrivateUsage]
from src.engine.state import initial_state
from src.engine.workflow import HttpNode
from src.executors.http import HttpExecutor, HttpNodeError


def _http_node(**data: Any) -> HttpNode:
    return HttpNode.model_validate(
        {
            "id": "h",
            "type": "http",
            "position": {"x": 0, "y": 0},
            "data": {"label": "H", **data},
        }
    )


async def test_http_get_returns_json(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/api/item",
        method="GET",
        json={"id": 1, "title": "ok"},
    )
    node = _http_node(httpMethod="GET", httpUrl="https://example.test/api/item")
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"id": 1, "title": "ok"}
    assert delta["current_node_id"] == "h"
    assert delta["node_results"]["h"]["status"] == "completed"


async def test_http_post_with_json_body(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/echo",
        method="POST",
        json={"echoed": True},
    )
    node = _http_node(
        httpMethod="POST",
        httpUrl="https://example.test/echo",
        httpBody={"hello": "world"},
    )
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"echoed": True}
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    # httpx serialises dict bodies as JSON when json= is passed
    assert b'"hello"' in req.content


async def test_http_post_with_string_body(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/raw",
        method="POST",
        text="accepted",
    )
    node = _http_node(
        httpMethod="POST",
        httpUrl="https://example.test/raw",
        httpBody="raw-payload",
    )
    delta = await HttpExecutor(node).arun(initial_state())
    # Non-JSON response → returned as text
    assert delta["variables"]["lastOutput"] == "accepted"


async def test_http_non_2xx_raises(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/bad",
        method="GET",
        status_code=500,
        text="boom",
    )
    node = _http_node(httpMethod="GET", httpUrl="https://example.test/bad")
    with pytest.raises(HttpNodeError, match="500"):
        await HttpExecutor(node).arun(initial_state())


async def test_http_response_path_dot_notation(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/nested",
        method="GET",
        json={"data": {"result": {"score": 99}}},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://example.test/nested",
        responsePath="data.result.score",
    )
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == 99


async def test_http_header_variable_substitution(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/",
        method="GET",
        json={"ok": True},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://example.test/",
        httpHeaders={"X-Token": "Bearer {{token}}"},
    )
    state = initial_state()
    state["variables"]["token"] = "abc"
    await HttpExecutor(node).arun(state)
    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    assert req.headers.get("x-token") == "Bearer abc"


async def test_http_url_variable_substitution(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnknownParameterType]
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://example.test/users/42",
        method="GET",
        json={"id": 42},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://example.test/users/{{user_id}}",
    )
    state = initial_state()
    state["variables"]["user_id"] = 42
    delta = await HttpExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == {"id": 42}


async def test_http_executor_is_registered() -> None:
    from src.executors.base import build_executor
    import src.executors.http  # noqa: F401  # pyright: ignore[reportUnusedImport]
    node = _http_node(httpMethod="GET", httpUrl="https://x/")
    executor = build_executor(node)
    assert isinstance(executor, HttpExecutor)
```

Note: the `_current_db` import is a vestige from copying the base-test shape; keep only if you use it for a fixture. If not referenced anywhere in the file, remove it.

- [ ] **Step 3: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_http_executor.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement `src/executors/http.py`**

```python
"""HTTP node executor.

Fires a single HTTP request and returns the parsed response body. Non-2xx
responses raise HttpNodeError; that bubbles up through LangGraphExecutor
as a failed execution.

See Phase 4a spec §6.
"""

import logging
from typing import Any

import httpx

from src.engine.state import WorkflowStateDict
from src.engine.workflow import HttpNode
from src.executors.base import register_executor
from src.variable_substitution import substitute, substitute_in_value

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


class HttpNodeError(RuntimeError):
    """Raised when the HTTP node gets a non-2xx response or transport failure."""


def _dot_path(value: Any, path: str) -> Any:
    """Walk a dot-separated path into a nested dict/list.  Missing → None."""
    cur = value
    for segment in path.split("."):
        if isinstance(cur, dict) and segment in cur:
            cur = cur[segment]
        elif isinstance(cur, list) and segment.isdigit():
            idx = int(segment)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


@register_executor("http")
class HttpExecutor:
    def __init__(self, node: HttpNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        method = (self.node.data.http_method or "GET").upper()
        raw_url = self.node.data.http_url or ""
        if not raw_url:
            raise HttpNodeError(f"http node {self.node.id!r} has no httpUrl")
        url = substitute(raw_url, state)
        headers = substitute_in_value(self.node.data.http_headers or {}, state)
        body_raw = substitute_in_value(self.node.data.http_body, state)

        body_kwargs: dict[str, Any] = {}
        if isinstance(body_raw, (dict, list)):
            body_kwargs["json"] = body_raw
        elif isinstance(body_raw, str) and body_raw:
            body_kwargs["content"] = body_raw

        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                resp = await client.request(
                    method, url, headers=headers, **body_kwargs
                )
        except httpx.HTTPError as exc:
            raise HttpNodeError(
                f"http node {self.node.id!r} transport failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise HttpNodeError(
                f"http node {self.node.id!r} got HTTP {resp.status_code} from "
                f"{url!r}: {resp.text[:200]}"
            )

        content_type = resp.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            parsed: Any = resp.json()
        else:
            parsed = resp.text

        if self.node.data.response_path:
            parsed = _dot_path(parsed, self.node.data.response_path)

        return {
            "variables": {"lastOutput": parsed},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"method": method, "url": url},
                    "output": parsed,
                }
            },
        }


__all__ = ["HttpExecutor", "HttpNodeError"]
```

- [ ] **Step 5: Register side-effect import in `src/engine/graph_builder.py`**

Read the file. Near the other `from src.executors import ... # noqa: F401 …` lines, add:

```python
from src.executors import http as _http_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

(Alphabetically: after `agent`, before `mcp`.)

- [ ] **Step 6: Move sentinel tests from `http`/`Phase 4` → `user-approval`/`Phase 5`**

Read each of the three files. Find the test that currently uses `"http"` as the unshipped-executor sentinel. Change:

- node `type: "http"` → `type: "user-approval"`
- assertion `"'http'" in str(exc)` → `"'user-approval'" in str(exc)`
- assertion `"Phase 4" in str(exc)` → `"Phase 5" in str(exc)`

The three files + test names:
- `tests/unit/executors/test_registry.py::test_unshipped_type_raises_with_phase_hint`
- `tests/unit/engine/test_graph_builder.py::test_build_graph_rejects_unshipped_executor_type`
- `tests/unit/engine/test_langgraph_executor.py::test_run_marks_failed_on_exception`

For `test_langgraph_executor.py`, the bad-workflow node fixture also needs `"type": "user-approval"` and the assertion checks `"Phase 5" in update_kwargs["error"]`.

Also update the `UserApprovalNode` import if required (it's already declared in Phase 1's workflow.py).

- [ ] **Step 7: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_http_executor.py tests/unit/executors/test_registry.py tests/unit/engine/test_graph_builder.py tests/unit/engine/test_langgraph_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 8 new + 3 updated sentinel tests pass; all prior pass; overall +8 (~272).

```bash
git add src/engine/workflow.py src/engine/graph_builder.py \
        src/executors/http.py \
        tests/unit/executors/test_http_executor.py \
        tests/unit/executors/test_registry.py \
        tests/unit/engine/test_graph_builder.py \
        tests/unit/engine/test_langgraph_executor.py
git commit -m "feat(executors): http — HTTP request node (Phase 4a)

One request, parsed response body:
  - Method / URL / headers / body all accept {{...}} substitution
    (headers + body recurse via substitute_in_value)
  - Dict/list body → JSON; string body → raw; None → no body
  - JSON response parsed by Content-Type; otherwise returned as text
  - responsePath dot-notation extracts into nested response (no brackets,
    no wildcards — chain a transform node if you need more)
  - Non-2xx and transport failures raise HttpNodeError which surfaces
    as the execution error

HttpNodeData gains responsePath field (Phase 1's shape was the stub).

Sentinel tests move: 'http'/'Phase 4' → 'user-approval'/'Phase 5'
across three files since http has now shipped.

See Phase 4a spec §6.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `set-state` executor

**Files:**
- Create: `src/executors/set_state.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_set_state_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/executors/test_set_state_executor.py`:

```python
"""Tests for the set-state executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import SetStateNode
from src.executors.set_state import SetStateExecutor


def _set_state_node(**data: Any) -> SetStateNode:
    return SetStateNode.model_validate(
        {
            "id": "s",
            "type": "set-state",
            "position": {"x": 0, "y": 0},
            "data": {"label": "S", **data},
        }
    )


async def test_set_state_writes_variable() -> None:
    node = _set_state_node(stateKey="name", stateValue="Ada")
    delta = await SetStateExecutor(node).arun(initial_state())
    assert delta["variables"]["name"] == "Ada"
    assert delta["variables"]["lastOutput"] == "Ada"
    assert delta["current_node_id"] == "s"
    assert delta["node_results"]["s"]["status"] == "completed"


async def test_set_state_substitutes_in_value() -> None:
    node = _set_state_node(
        stateKey="greeting",
        stateValue="hello {{name}}",
    )
    state = initial_state()
    state["variables"]["name"] = "Ada"
    delta = await SetStateExecutor(node).arun(state)
    assert delta["variables"]["greeting"] == "hello Ada"


async def test_set_state_supports_nested_value() -> None:
    node = _set_state_node(
        stateKey="config",
        stateValue={"apiKey": "{{key}}", "retries": 3},
    )
    state = initial_state()
    state["variables"]["key"] = "sk-1"
    delta = await SetStateExecutor(node).arun(state)
    assert delta["variables"]["config"] == {"apiKey": "sk-1", "retries": 3}


async def test_set_state_missing_key_raises() -> None:
    node = _set_state_node(stateValue="anything")
    with pytest.raises(ValueError, match="stateKey"):
        await SetStateExecutor(node).arun(initial_state())


async def test_set_state_executor_is_registered() -> None:
    from src.executors.base import build_executor
    import src.executors.set_state  # noqa: F401  # pyright: ignore[reportUnusedImport]
    node = _set_state_node(stateKey="x", stateValue=1)
    executor = build_executor(node)
    assert isinstance(executor, SetStateExecutor)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_set_state_executor.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/executors/set_state.py`**

```python
"""set-state node executor.

Writes a (optionally templated) value into state.variables[stateKey].
Also aliases the value as `variables.lastOutput` so downstream nodes can
consume it without naming the key explicitly.

See Phase 4a spec §7.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import SetStateNode
from src.executors.base import register_executor
from src.variable_substitution import substitute_in_value


@register_executor("set-state")
class SetStateExecutor:
    def __init__(self, node: SetStateNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        state_key = self.node.data.state_key
        if not state_key:
            raise ValueError(
                f"set-state node {self.node.id!r} requires stateKey"
            )
        resolved = substitute_in_value(self.node.data.state_value, state)
        return {
            "variables": {
                state_key: resolved,
                "lastOutput": resolved,
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {state_key: resolved},
                    "output": resolved,
                }
            },
        }


__all__ = ["SetStateExecutor"]
```

- [ ] **Step 4: Register side-effect import in `graph_builder.py`**

Add (alphabetically between existing side-effect imports):

```python
from src.executors import set_state as _set_state_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_set_state_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 5 new pass; overall +5 (~277).

```bash
git add src/executors/set_state.py src/engine/graph_builder.py \
        tests/unit/executors/test_set_state_executor.py
git commit -m "feat(executors): set-state — direct state mutation (Phase 4a)

Writes stateValue (templated via substitute_in_value) into
variables[stateKey] AND aliases as lastOutput so downstream nodes can
consume without naming the key.

Missing stateKey → ValueError.

See Phase 4a spec §7.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `transform` executor

**Files:**
- Create: `src/executors/transform.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_transform_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/executors/test_transform_executor.py`:

```python
"""Tests for the transform executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import TransformNode
from src.executors.transform import TransformExecutor, TransformNodeError


def _transform_node(**data: Any) -> TransformNode:
    return TransformNode.model_validate(
        {
            "id": "t",
            "type": "transform",
            "position": {"x": 0, "y": 0},
            "data": {"label": "T", **data},
        }
    )


async def test_transform_arithmetic() -> None:
    node = _transform_node(transformScript="variables['x'] * 2")
    state = initial_state()
    state["variables"]["x"] = 21
    delta = await TransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == 42


async def test_transform_last_output_shorthand() -> None:
    node = _transform_node(transformScript="lastOutput.upper()")
    state = initial_state()
    state["variables"]["lastOutput"] = "hello"
    delta = await TransformExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == "HELLO"


async def test_transform_missing_script_raises() -> None:
    node = _transform_node()  # no transformScript
    with pytest.raises(ValueError, match="transformScript"):
        await TransformExecutor(node).arun(initial_state())


async def test_transform_syntax_error_raises() -> None:
    node = _transform_node(transformScript="1 +* 2")
    with pytest.raises(TransformNodeError):
        await TransformExecutor(node).arun(initial_state())


async def test_transform_dunder_access_blocked() -> None:
    node = _transform_node(transformScript="variables['x'].__class__")
    state = initial_state()
    state["variables"]["x"] = "hello"
    with pytest.raises(TransformNodeError):
        await TransformExecutor(node).arun(state)


async def test_transform_executor_is_registered() -> None:
    from src.executors.base import build_executor
    import src.executors.transform  # noqa: F401  # pyright: ignore[reportUnusedImport]
    node = _transform_node(transformScript="1")
    executor = build_executor(node)
    assert isinstance(executor, TransformExecutor)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_transform_executor.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/executors/transform.py`**

```python
"""transform node executor.

Evaluates a single simpleeval expression over state and writes the
result to lastOutput.  No variable substitution on the expression itself
— the whole point is that the expression reads state directly via the
`variables` / `lastOutput` / `node_results` names.

See Phase 4a spec §8.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import TransformNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor


class TransformNodeError(RuntimeError):
    """Wraps EvalError with node-id context."""


@register_executor("transform")
class TransformExecutor:
    def __init__(self, node: TransformNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.transform_script
        if not expr:
            raise ValueError(
                f"transform node {self.node.id!r} requires transformScript"
            )
        try:
            result: Any = evaluate(expr, state)
        except EvalError as exc:
            raise TransformNodeError(
                f"transform node {self.node.id!r}: {exc}"
            ) from exc

        return {
            "variables": {"lastOutput": result},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"expression": expr},
                    "output": result,
                }
            },
        }


__all__ = ["TransformExecutor", "TransformNodeError"]
```

- [ ] **Step 4: Register side-effect import**

Add to `src/engine/graph_builder.py`:

```python
from src.executors import transform as _transform_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_transform_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 6 new pass; overall +6 (~283).

```bash
git add src/executors/transform.py src/engine/graph_builder.py \
        tests/unit/executors/test_transform_executor.py
git commit -m "feat(executors): transform — simpleeval expression (Phase 4a)

Single-expression transform over state:
  - Reads via variables / lastOutput / node_results (no builtins, no
    imports, no dunder — per ADR-0012)
  - Result → variables.lastOutput

Missing transformScript → ValueError. simpleeval failures (undefined
name, syntax error, blocked dunder) wrap as TransformNodeError.

Deliberate OAB deviation: Python expression syntax, not JS. No arrow
functions, no template literals, no ?. operator. Error messages name
the expression + underlying cause for mechanical translation.

See Phase 4a spec §8.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `data-transform` executor

**Files:**
- Modify: `src/engine/workflow.py` (tighten DataTransformNodeData)
- Create: `src/executors/data_transform.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_data_transform_executor.py`

- [ ] **Step 1: Tighten `DataTransformNodeData` in `src/engine/workflow.py`**

Read the file. Find:

```python
class DataTransformNodeData(BaseNodeData):
    # OAB field shape: loose until Phase 4 inspects lib/workflow/executors/data-transform.ts
    # TODO(phase-4): tighten against OAB's DataTransform node fields.
    config: dict[str, Any] = Field(default_factory=dict)
```

Replace with:

```python
class DataTransformNodeData(BaseNodeData):
    operation: str = "map"  # map | filter | reduce
    collection: str = ""  # simpleeval expression that yields an iterable
    expression: str = ""  # per-item expression
    item_var: str = Field(default="item", alias="itemVar")
    initial: Any | None = None  # used only for reduce
```

- [ ] **Step 2: Write failing tests**

Create `tests/unit/executors/test_data_transform_executor.py`:

```python
"""Tests for the data-transform executor."""

from typing import Any

import pytest

from src.engine.state import initial_state
from src.engine.workflow import DataTransformNode
from src.executors.data_transform import (
    DataTransformExecutor,
    DataTransformNodeError,
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
    from src.executors.base import build_executor
    import src.executors.data_transform  # noqa: F401  # pyright: ignore[reportUnusedImport]
    node = _dt_node(
        operation="map",
        collection="[]",
        expression="item",
    )
    executor = build_executor(node)
    assert isinstance(executor, DataTransformExecutor)
```

- [ ] **Step 3: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_data_transform_executor.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement `src/executors/data_transform.py`**

```python
"""data-transform node executor — map / filter / reduce via simpleeval.

Operation = 'map' | 'filter' | 'reduce'.  For each item, evaluate the
per-item expression with the item bound to `itemVar` (default 'item').
Reduce additionally exposes `acc` in scope.

See Phase 4a spec §9.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import DataTransformNode
from src.executors._eval import EvalError, evaluate
from src.executors.base import register_executor


class DataTransformNodeError(RuntimeError):
    """Raised for unknown operation, non-iterable collection, or eval failure."""


_SUPPORTED_OPS = {"map", "filter", "reduce"}


@register_executor("data-transform")
class DataTransformExecutor:
    def __init__(self, node: DataTransformNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        op = self.node.data.operation
        if op not in _SUPPORTED_OPS:
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} operation {op!r} not "
                f"supported (need map / filter / reduce)"
            )
        if not self.node.data.collection:
            raise ValueError(
                f"data-transform node {self.node.id!r} requires collection expression"
            )
        if not self.node.data.expression:
            raise ValueError(
                f"data-transform node {self.node.id!r} requires expression"
            )

        try:
            coll = evaluate(self.node.data.collection, state)
        except EvalError as exc:
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} collection: {exc}"
            ) from exc

        if not isinstance(coll, (list, tuple)):
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} collection evaluated to "
                f"non-iterable type {type(coll).__name__}"
            )

        item_var = self.node.data.item_var
        expr = self.node.data.expression

        output: Any
        try:
            if op == "map":
                output = [
                    evaluate(expr, state, extra_names={item_var: x}) for x in coll
                ]
            elif op == "filter":
                output = [
                    x for x in coll
                    if evaluate(expr, state, extra_names={item_var: x})
                ]
            else:  # reduce
                acc: Any = self.node.data.initial
                for x in coll:
                    acc = evaluate(
                        expr,
                        state,
                        extra_names={item_var: x, "acc": acc},
                    )
                output = acc
        except EvalError as exc:
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} per-item expression: {exc}"
            ) from exc

        return {
            "variables": {"lastOutput": output},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {
                        "operation": op,
                        "expression": expr,
                        "itemVar": item_var,
                    },
                    "output": output,
                }
            },
        }


__all__ = ["DataTransformExecutor", "DataTransformNodeError"]
```

- [ ] **Step 5: Register side-effect import**

Add to `src/engine/graph_builder.py`:

```python
from src.executors import data_transform as _data_transform_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

- [ ] **Step 6: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_data_transform_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 8 new pass; overall +8 (~291).

```bash
git add src/engine/workflow.py src/executors/data_transform.py \
        src/engine/graph_builder.py \
        tests/unit/executors/test_data_transform_executor.py
git commit -m "feat(executors): data-transform — map/filter/reduce (Phase 4a)

Three operations over a collection:
  - map: [expression(item) for item in collection]
  - filter: [item for item in collection if expression(item)]
  - reduce: acc starting from initial; expression sees item + acc

DataTransformNodeData replaces Phase 1 stub (a single 'config' dict)
with proper fields: operation, collection, expression, itemVar, initial.

Non-list collection → DataTransformNodeError.  Unknown operation →
DataTransformNodeError.  Per-item eval failure → DataTransformNodeError
wrapping EvalError.

See Phase 4a spec §9, ADR-0012.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `extract` executor

**Files:**
- Modify: `src/engine/workflow.py` (ExtractNodeData gains `input_text`)
- Create: `src/executors/extract.py`
- Modify: `src/engine/graph_builder.py` (side-effect import)
- Create: `tests/unit/executors/test_extract_executor.py`

- [ ] **Step 1: Amend `ExtractNodeData`**

Read the file. Find:

```python
class ExtractNodeData(BaseNodeData):
    extract_config: dict[str, Any] | None = Field(default=None, alias="extractConfig")
    extract_tool: str | None = Field(default=None, alias="extractTool")
    json_schema: dict[str, Any] | None = Field(default=None, alias="jsonSchema")
```

Add `input_text` and `model`:

```python
class ExtractNodeData(BaseNodeData):
    extract_config: dict[str, Any] | None = Field(default=None, alias="extractConfig")
    extract_tool: str | None = Field(default=None, alias="extractTool")
    json_schema: dict[str, Any] | None = Field(default=None, alias="jsonSchema")
    input_text: str | None = Field(default=None, alias="input")
    model: str | None = None
```

- [ ] **Step 2: Write failing tests**

Create `tests/unit/executors/test_extract_executor.py`:

```python
"""Tests for the extract executor."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from src.engine.state import initial_state
from src.engine.workflow import ExtractNode
from src.executors.extract import ExtractExecutor, ExtractNodeError


def _extract_node(**data: Any) -> ExtractNode:
    return ExtractNode.model_validate(
        {
            "id": "e",
            "type": "extract",
            "position": {"x": 0, "y": 0},
            "data": {"label": "E", **data},
        }
    )


async def test_extract_with_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """structured_invoke returns a dict → extract passes it through."""
    from src.llm import structured_output as so

    async def _fake_invoke(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"title": "Hello", "count": 3}

    monkeypatch.setattr(so, "structured_invoke", _fake_invoke)
    from src.llm import providers

    class _StubModel:
        async def ainvoke(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
            return AIMessage(content="{}")
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: _StubModel())  # pyright: ignore[reportUnknownLambdaType]

    node = _extract_node(
        input="Hello 3",
        jsonSchema={"type": "object", "properties": {"title": {"type": "string"}}},
    )
    delta = await ExtractExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"title": "Hello", "count": 3}


async def test_extract_uses_last_output_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    from src.llm import structured_output as so

    async def _fake_invoke(_model: Any, messages: Any, **kw: Any) -> dict[str, Any]:
        captured["prompt"] = messages[0].content if messages else ""
        return {"ok": True}

    monkeypatch.setattr(so, "structured_invoke", _fake_invoke)
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: object())  # pyright: ignore[reportUnknownLambdaType]

    node = _extract_node(jsonSchema={"type": "object"})
    state = initial_state()
    state["variables"]["lastOutput"] = "fallback input"
    await ExtractExecutor(node).arun(state)
    assert "fallback input" in captured["prompt"]


async def test_extract_empty_input_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # No input_text, and lastOutput is empty
    from src.llm import structured_output as so
    monkeypatch.setattr(so, "structured_invoke", AsyncMock())
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: object())  # pyright: ignore[reportUnknownLambdaType]

    node = _extract_node(jsonSchema={"type": "object"})
    with pytest.raises(ExtractNodeError, match="empty input"):
        await ExtractExecutor(node).arun(initial_state())


async def test_extract_coerces_aimessage_to_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.llm import structured_output as so

    async def _fake_invoke(*a: Any, **kw: Any) -> Any:
        return AIMessage(content='{"wrapped": true}')

    monkeypatch.setattr(so, "structured_invoke", _fake_invoke)
    from src.llm import providers
    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: object())  # pyright: ignore[reportUnknownLambdaType]

    node = _extract_node(input="irrelevant", jsonSchema=None)
    delta = await ExtractExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"wrapped": True}


async def test_extract_executor_is_registered() -> None:
    from src.executors.base import build_executor
    import src.executors.extract  # noqa: F401  # pyright: ignore[reportUnusedImport]
    node = _extract_node(input="x", jsonSchema={"type": "object"})
    executor = build_executor(node)
    assert isinstance(executor, ExtractExecutor)
```

- [ ] **Step 3: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_extract_executor.py -v
```
Expected: ImportError.

- [ ] **Step 4: Implement `src/executors/extract.py`**

```python
"""extract node executor.

LLM structured-output extraction. Reuses Phase 2's `structured_invoke`
so the same JSON-mode / schema logic backs both Agent JSON output and
Extract nodes.

See Phase 4a spec §10.
"""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from src.engine.context import get_current_langsmith
from src.engine.state import WorkflowStateDict
from src.engine.workflow import ExtractNode
from src.executors.base import register_executor
from src.llm import providers as _providers
from src.llm.structured_output import structured_invoke
from src.variable_substitution import substitute

logger = logging.getLogger(__name__)

DEFAULT_EXTRACT_MODEL = "anthropic/claude-haiku-4-5-20251001"


class ExtractNodeError(RuntimeError):
    """Raised when extract receives empty input or the LLM response can't be parsed."""


def _coerce_to_dict(result: Any) -> Any:
    """Pull a dict out of structured_invoke's result (may be AIMessage)."""
    if isinstance(result, AIMessage):
        content = result.content
        if isinstance(content, str):
            try:
                return json.loads(content)
            except (json.JSONDecodeError, ValueError):
                return content
        return content
    return result


@register_executor("extract")
class ExtractExecutor:
    def __init__(self, node: ExtractNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        template = self.node.data.input_text or "{{lastOutput}}"
        input_text = substitute(template, state)
        if not input_text.strip():
            raise ExtractNodeError(
                f"extract node {self.node.id!r} has empty input"
            )

        chat_model = _providers.build_chat_model(
            self.node.data.model or DEFAULT_EXTRACT_MODEL,
            langsmith_config=get_current_langsmith(),
        )
        messages = [
            HumanMessage(content=f"Extract structured data from:\n\n{input_text}"),
        ]
        result = await structured_invoke(
            chat_model,
            messages,
            schema=self.node.data.json_schema,
            json_mode=self.node.data.json_schema is None,
        )
        parsed = _coerce_to_dict(result)

        return {
            "variables": {"lastOutput": parsed},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"input": input_text[:200]},
                    "output": parsed,
                }
            },
        }


__all__ = ["DEFAULT_EXTRACT_MODEL", "ExtractExecutor", "ExtractNodeError"]
```

- [ ] **Step 5: Register side-effect import**

Add to `src/engine/graph_builder.py`:

```python
from src.executors import extract as _extract_executor  # noqa: F401  # pyright: ignore[reportUnusedImport]
```

- [ ] **Step 6: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/executors/test_extract_executor.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 5 new pass; overall +5 (~296).

```bash
git add src/engine/workflow.py src/executors/extract.py \
        src/engine/graph_builder.py \
        tests/unit/executors/test_extract_executor.py
git commit -m "feat(executors): extract — LLM structured output (Phase 4a)

Reuses Phase 2's structured_invoke so one codepath backs both Agent
JSON output and the Extract node.

Defaults:
  - Model: anthropic/claude-haiku-4-5-20251001 (matches agent default)
  - Input: {{lastOutput}} — use prior node's output if not overridden
  - Schema None → json_mode=True (schema-less JSON output)

Error modes:
  - Empty input → ExtractNodeError('empty input')
  - AIMessage result → unwrap + json.loads; on failure return raw string

ExtractNodeData gains input_text + model fields. Existing jsonSchema /
extractConfig / extractTool left untouched for future use.

See Phase 4a spec §10.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Integration test — 4-node linear workflow

**File:**
- Create: `tests/integration/test_linear_multinode.py`

Verifies `Start → HTTP → Extract → Set-State → End` end-to-end against real Neon + real Anthropic + real https://jsonplaceholder.typicode.com (public test API, no keys).

- [ ] **Step 1: Write**

Create `tests/integration/test_linear_multinode.py`:

```python
"""Integration — Start → HTTP → Extract → Set-State → End.

Real Neon + real Anthropic + public jsonplaceholder HTTP endpoint.
"""

import asyncio
import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 120.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


async def test_linear_workflow_http_extract_set_state(client: AsyncClient) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    wf = await client.post(
        "/workflows",
        json={
            "name": "Phase 4a linear workflow",
            "nodes": [
                {
                    "id": "s",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "S"},
                },
                {
                    "id": "h",
                    "type": "http",
                    "position": {"x": 100, "y": 0},
                    "data": {
                        "label": "H",
                        "httpMethod": "GET",
                        "httpUrl": "https://jsonplaceholder.typicode.com/todos/1",
                    },
                },
                {
                    "id": "x",
                    "type": "extract",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "label": "X",
                        "jsonSchema": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "completed": {"type": "boolean"},
                            },
                            "required": ["title", "completed"],
                        },
                    },
                },
                {
                    "id": "ss",
                    "type": "set-state",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "SS",
                        "stateKey": "todoSummary",
                        "stateValue": "{{lastOutput.title}}",
                    },
                },
                {
                    "id": "e",
                    "type": "end",
                    "position": {"x": 400, "y": 0},
                    "data": {"label": "E"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "s", "target": "h"},
                {"id": "e2", "source": "h", "target": "x"},
                {"id": "e3", "source": "x", "target": "ss"},
                {"id": "e4", "source": "ss", "target": "e"},
            ],
        },
    )
    assert wf.status_code == 201, wf.text
    start = await client.post(
        "/executions",
        json={"workflowId": wf.json()["id"], "input": ""},
    )
    final = await _poll_until_terminal(client, start.json()["id"])
    assert final["status"] == "completed", f"Got: {final}"
    variables = final.get("variables") or {}
    assert isinstance(variables, dict)
    summary = variables.get("todoSummary")
    assert isinstance(summary, str) and len(summary) > 0
```

- [ ] **Step 2: Quality gates + commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
.venv/Scripts/python -m pytest tests/integration/test_linear_multinode.py --collect-only -q
```
Expected: 0 ruff errors, 0 pyright errors, non-integration still green, 1 integration test collected.

```bash
git add tests/integration/test_linear_multinode.py
git commit -m "test(integration): HTTP → Extract → Set-State multi-node workflow

Real Neon + real Anthropic + public jsonplaceholder endpoint.
Verifies:
  - HTTP node fires the request and parses JSON response → lastOutput
  - Extract node coerces lastOutput into the declared schema
  - Set-State node writes variables.todoSummary via {{lastOutput.title}}
    template — proving dot-path variable access works through state

Skips without ANTHROPIC_API_KEY.

Controller will run at phase-exit:
  .venv/Scripts/python -m pytest -m integration \\
      tests/integration/test_linear_multinode.py

See Phase 4a spec §12.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: OAB regression port — http node

**File:**
- Create: `tests/regression/test_oab_http_node.py`

Ports the simplest node spec from OAB (`lib/workflow/nodes/__tests__/http.spec.ts` or equivalent) against Composer's API. Mocked HTTP — no real external calls.

- [ ] **Step 1: Probe OAB (optional)**

If you want to confirm the test shape matches an existing OAB spec, check whether such a file exists:

```bash
ls D:/GitHub/open-agent-builder/tests/ 2>/dev/null | grep -i http || echo "(no direct http spec; port from workflow tests)"
```

It's fine to author a from-scratch regression that exercises the same behavioural contract OAB's executor guarantees (status-code rejection, JSON parsing, headers substitution). Name the test honestly.

- [ ] **Step 2: Write**

Create `tests/regression/test_oab_http_node.py`:

```python
"""Regression — HTTP node behavioural contract (ported from OAB parity).

OAB reference: lib/workflow/executors/http.ts
  - Non-2xx → execution failure
  - JSON response → parsed; otherwise text
  - Header / URL / body accept template substitution

Mocked — no real external calls.
"""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import HttpNode
from src.executors.http import HttpExecutor, HttpNodeError

pytestmark = pytest.mark.integration


def _http_node(**data: Any) -> HttpNode:
    return HttpNode.model_validate(
        {
            "id": "h",
            "type": "http",
            "position": {"x": 0, "y": 0},
            "data": {"label": "H", **data},
        }
    )


async def test_oab_regression_json_response_parsed(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.test/v1/thing",
        method="GET",
        json={"id": 1, "status": "ok"},
    )
    node = _http_node(
        httpMethod="GET", httpUrl="https://api.example.test/v1/thing"
    )
    delta = await HttpExecutor(node).arun(initial_state())
    assert delta["variables"]["lastOutput"] == {"id": 1, "status": "ok"}


async def test_oab_regression_non_2xx_fails(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.test/fail",
        method="GET",
        status_code=500,
        text="Internal Server Error",
    )
    node = _http_node(
        httpMethod="GET", httpUrl="https://api.example.test/fail"
    )
    with pytest.raises(HttpNodeError, match="500"):
        await HttpExecutor(node).arun(initial_state())


async def test_oab_regression_substitutes_template_in_url(
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://api.example.test/users/42",
        method="GET",
        json={"id": 42},
    )
    node = _http_node(
        httpMethod="GET",
        httpUrl="https://api.example.test/users/{{user_id}}",
    )
    state = initial_state()
    state["variables"]["user_id"] = 42
    delta = await HttpExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == {"id": 42}
```

- [ ] **Step 3: Quality gates + commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest tests/regression/test_oab_http_node.py -v
```
Expected: 3 regression tests pass.

```bash
git add tests/regression/test_oab_http_node.py
git commit -m "test(regression): HTTP node behavioural contract

Three mocked regressions capture OAB's HTTP executor contract:
  1. JSON response → parsed output
  2. Non-2xx → HttpNodeError (mapped to execution failure by orchestrator)
  3. Template substitution in URL works (mirrors OAB's handlebars-like
     behaviour via Composer's {{...}} substitution)

Mocked via pytest-httpx — no external calls. Serves as the canary if
a future refactor silently drops any of these three behaviours.

See Phase 4a spec §12.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0012 backfill

**Authorized for this task:** `CHANGELOG.md`, `CLAUDE.md` phase-status table, `docs/design/decisions.md` ADR-0012 `Implemented by` line.

- [ ] **Step 1: Verify exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
All four green.

Run the Phase 4a integration + regression suite (controller, not subagent):
```bash
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_linear_multinode.py',
     'tests/regression/test_oab_http_node.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```

- [ ] **Step 2: Add CHANGELOG Phase 4a section**

Insert above the Phase 3b section:

```markdown
### Phase 4a — Linear Executors (http, set-state, transform, data-transform, extract) (2026-04-21)

#### Added
- [Phase 4a design spec](docs/superpowers/specs/2026-04-21-phase-4a-linear-executors-design.md) + ADR-0012.
- `src/variable_substitution.py substitute_in_value` — recursive `{{...}}` over dict/list/str.
- `src/executors/_eval.py` — simpleeval wrapper, the single eval primitive (ADR-0012).
- `src/executors/http.py` — HTTP request node (JSON/text response, dot-path extraction, template substitution on URL/headers/body).
- `src/executors/set_state.py` — writes templated values into `state.variables[stateKey]`.
- `src/executors/transform.py` — single simpleeval expression over state.
- `src/executors/data_transform.py` — map / filter / reduce with per-item `simpleeval` evaluation.
- `src/executors/extract.py` — LLM structured-output extraction reusing Phase 2's `structured_invoke`.
- Integration test: `Start → HTTP → Extract → Set-State → End` against real Neon + real Anthropic + public jsonplaceholder endpoint.
- Regression test: OAB HTTP-node behavioural contract (JSON parsing, non-2xx failure, URL templating).

#### Changed
- `src/engine/workflow.py`: `HttpNodeData` gains `responsePath`; `DataTransformNodeData` tightened from stub `config: dict` to proper fields (`operation`, `collection`, `expression`, `itemVar`, `initial`); `ExtractNodeData` gains `input_text` + `model`.
- `src/engine/graph_builder.py`: five new side-effect imports for the new executors.
- Sentinel tests move from `http`/`Phase 4` → `user-approval`/`Phase 5` across three files.

#### Deliberate deviations from OAB
- `transform` / `data-transform` use `simpleeval` Python expression syntax, not JS `vm.runInNewContext`. No arrow functions, no template literals, no `?.`. `EvalError` names the failing expression + cause for mechanical translation.
- `http` does not follow OAB's retry/backoff config; Phase 9 (hardening) can introduce it.
- `extract` runs on Phase 2's `structured_invoke`; the LangSmith config threading (Phase 3b fix #6) applies automatically.
```

- [ ] **Step 3: Update CLAUDE.md phase table**

Change:
```markdown
| 3b — MCP OAuth (Highspot + the six fixes) | ✅ Complete | All six OAB lessons encoded; real-Highspot MCP stack verified to auth boundary |
| 4 — HTTP, Transform, Extract, If/Else, While, Set-State | ⏭ Next | |
```
To:
```markdown
| 3b — MCP OAuth (Highspot + the six fixes) | ✅ Complete | All six OAB lessons encoded; real-Highspot MCP stack verified to auth boundary |
| 4a — Linear executors (http, set-state, transform, data-transform, extract) | ✅ Complete | 5 executors + simpleeval wrapper + recursive substitution + integration/regression |
| 4b — Control-flow (if-else, while) | ⏭ Next | Needs graph_builder surgery for conditional/loop edges |
```

- [ ] **Step 4: Backfill ADR-0012 `Implemented by`**

In `docs/design/decisions.md`, change:
```markdown
**Implemented by.** Phase 4a (commits TBD).
```
To:
```markdown
**Implemented by.** Phase 4a (commits `<first-sha>`..`<last-sha>` on `main`, 2026-04-21).
```

Get the range with:
```bash
git log --oneline bf9e8e9..HEAD
```
(starts just after Phase 3b exit).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-4a): mark Phase 4a complete

Five linear executors shipped + simpleeval wrapper locked in as the
single eval primitive (ADR-0012). Phase 4b (if-else + while) deferred
— it needs graph_builder surgery for conditional edges and is shipped
separately.

ADR-0012 Implemented by backfilled with commit range.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §5.1 substitute_in_value | Task 1 |
| §5.2 _eval.py | Task 2 |
| §6 http | Task 3 |
| §7 set-state | Task 4 |
| §8 transform | Task 5 |
| §9 data-transform | Task 6 |
| §10 extract | Task 7 |
| §11 sentinel test move | Task 3 (bundled with http) |
| §12.1 unit tests | Tasks 1–7 |
| §12.2 integration | Task 8 |
| §12.3 regression | Task 9 |
| §13 phase-exit checklist | Task 10 |

No placeholder steps. Type consistency check:
- `substitute_in_value(value, state) → Any` — used identically in http + set-state.
- `evaluate(expression, state, *, extra_names=None) → Any` — used identically in transform + data-transform.
- `register_executor("...")` decorator used identically on all five executors.
- Error class naming: `HttpNodeError`, `TransformNodeError`, `DataTransformNodeError`, `ExtractNodeError` — consistent `<Node>NodeError` pattern.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–10. Same cadence as Phases 1–3b. Task 10 runs the integration + regression suite against real Neon + real Anthropic + public HTTP before the phase-exit commit lands.
