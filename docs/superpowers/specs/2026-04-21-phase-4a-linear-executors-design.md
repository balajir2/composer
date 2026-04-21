# Phase 4a — Linear Executors (http, set-state, transform, data-transform, extract)

**Target exit date:** 2026-04-22
**Author:** Composer team
**Depends on:** Phase 1 (engine core), Phase 2 (LLM providers + `structured_invoke` + variable substitution), Phase 3a/3b complete.
**Split parent:** Phase 4 decomposed into 4a (linear) + 4b (control-flow / conditional edges).

---

## 1. Goal

Ship five node-type executors that each produce a state delta without changing graph topology:

| Node type | Responsibility |
|---|---|
| `http` | Call an external HTTP endpoint, return the parsed response body. |
| `set-state` | Write a templated value into `state.variables[stateKey]`. |
| `transform` | Evaluate a single `simpleeval` expression over state; return the result. |
| `data-transform` | Apply `map` / `filter` / `reduce` via `simpleeval` to a collection in state. |
| `extract` | LLM structured-output extraction against a JSON Schema; return the parsed dict. |

Each executor plugs into the same `@register_executor(type)` + `arun(state) -> delta` pattern used by Phases 1–3. None of them touches graph edges (that's Phase 4b's scope).

## 2. Non-goals for this phase

- Response streaming for `http` — bulk response body only; streaming lands in Phase 5 alongside SSE.
- JavaScript-compatible semantics for `transform` / `data-transform` expressions. OAB uses `vm.runInNewContext`; Composer uses `simpleeval`. We document deviations where they bite.
- Backpressure / rate-limiting / retries on HTTP. Single-shot. Exponential backoff is a later concern.
- JSONPath extraction beyond simple dot-paths (`a.b.c`) in `http.responsePath`. Bracket notation / filters / wildcards defer.
- Nested extraction in `extract` — top-level schema only; nested objects are produced but not recursively re-extracted.
- The six other unshipped types: `if-else`, `while` (Phase 4b), `user-approval` (Phase 5), `guardrails`, `note`, `vector-db`, `gamma-ai`, `arcade`, `join-chunks` (Phase 6).

## 3. Success criteria

Phase 4a exits when:

1. All unit tests pass: `ruff`, `ruff format --check`, `pyright --strict`, `pytest -m "not integration"` green on `main`.
2. The integration test `tests/integration/test_linear_multinode.py` runs end-to-end against real Neon + real Anthropic (chains `Start → HTTP → Extract → Set-State → End`).
3. OAB regression port for at least one `lib/workflow/nodes/*.spec.ts` file passes.
4. `grep -rn "Phase 4" src/executors/base.py` still shows only `if-else`, `while` mapped to Phase 4 — the other five have shipped and are in `_REGISTRY`.
5. The sentinel tests (`test_unshipped_type_raises_with_phase_hint`, `test_build_graph_rejects_unshipped_executor_type`, `test_run_marks_failed_on_exception`) use `"user-approval"` as the unshipped-executor sentinel.
6. CHANGELOG, CLAUDE.md phase table, and `docs/design/decisions.md` all reflect 4a ✅ Complete.

## 4. Module layout

```
src/
  variable_substitution.py       EXTENDED — adds `substitute_in_value(value, state)`
                                    for recursive dict/list/str traversal.
  executors/
    _eval.py                     NEW — simpleeval wrapper.  Single source of truth
                                    for any "evaluate a user-supplied expression
                                    over state" operation.  Shared across
                                    transform, data-transform, 4b's if-else, while.
    http.py                      NEW — HTTP request node.
    set_state.py                 NEW — direct state mutation.
    transform.py                 NEW — single-expression transform.
    data_transform.py            NEW — map / filter / reduce.
    extract.py                   NEW — LLM structured extraction.

  engine/
    graph_builder.py             CHANGED — add five side-effect imports:
                                    from src.executors import http, set_state,
                                    transform, data_transform, extract.

tests/
  unit/
    test_variable_substitution.py  EXTENDED — recursive substitute_in_value tests.
    executors/
      test_eval.py                 NEW — simpleeval wrapper unit tests.
      test_http_executor.py        NEW
      test_set_state_executor.py   NEW
      test_transform_executor.py   NEW
      test_data_transform_executor.py  NEW
      test_extract_executor.py     NEW
    engine/
      test_graph_builder.py        CHANGED — sentinel test moves to `user-approval`.
      test_langgraph_executor.py   CHANGED — sentinel test moves to `user-approval`.
    executors/
      test_registry.py             CHANGED — sentinel test moves to `user-approval`.

  integration/
    test_linear_multinode.py     NEW — 4-node workflow against real Neon +
                                    real Anthropic (HTTP → Extract → Set-State).

  regression/
    test_oab_http_transform.py   NEW — port of one OAB node spec.
```

Total: 2 extended files, 6 new `src/` files (5 executors + 1 eval helper), 7 new test files, 3 test files updated for the sentinel swap.

## 5. Shared primitives

### 5.1 `substitute_in_value` — recursive variable substitution

Phase 2's `substitute(template: str, state) -> str` handles a single string. HTTP bodies and headers are often dicts with templated leaves. Add a recursive helper:

```python
# src/variable_substitution.py  (extend)

def substitute_in_value(value: Any, state: WorkflowStateDict) -> Any:
    """Recursively apply {{...}} substitution over nested structures.

    Rules:
      - str: substitute via `substitute()`
      - dict: recurse into values, keys are left untouched
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

No protoype-pollution guard needed beyond what Phase 2 already has (the leaf `substitute()` does it). Keys stay untouched: a user writing `{"{{dynamic_key}}": "..."}` is attempting something weird; don't enable it.

### 5.2 `_eval.py` — simpleeval wrapper

One module. One public function. Enforces the security model.

```python
# src/executors/_eval.py  (new)

from typing import Any

from simpleeval import (
    AttributeDoesNotExist,
    FunctionNotDefined,
    InvalidExpression,
    NameNotDefined,
    NumberTooHigh,
    SimpleEval,
)

from src.engine.state import WorkflowStateDict


class EvalError(RuntimeError):
    """Any simpleeval failure — invalid expression, undefined name, etc."""


def _build_evaluator(state: WorkflowStateDict, *, extra_names: dict[str, Any] | None = None) -> SimpleEval:
    """Return a SimpleEval bound to the per-node scope.

    Scope names:
      - `variables` — state["variables"] dict (access via variables.foo or variables["foo"])
      - `lastOutput` — shorthand for variables["lastOutput"]
      - `node_results` — dict of prior node outputs (read-only view)
      - any extras passed (e.g., `item` for data-transform iteration)
    No builtins.  No imports.  No dunder attribute access.
    """
    names: dict[str, Any] = {
        "variables": dict(state.get("variables") or {}),
        "lastOutput": (state.get("variables") or {}).get("lastOutput"),
        "node_results": dict(state.get("node_results") or {}),
    }
    if extra_names:
        names.update(extra_names)
    e = SimpleEval(names=names)
    return e


def evaluate(
    expression: str,
    state: WorkflowStateDict,
    *,
    extra_names: dict[str, Any] | None = None,
) -> Any:
    """Evaluate a simpleeval expression over state.  Raises EvalError on failure.

    Reference: ADR-0012.
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
```

`simpleeval` is already declared in `pyproject.toml` per Phase 0. No deps change.

## 6. `http` executor

### 6.1 Node data shape

```python
class HttpNodeData(BaseNodeData):
    method: str = "GET"                         # GET/POST/PUT/DELETE/PATCH
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None                            # dict → JSON; str → raw; None → no body
    response_path: str | None = Field(default=None, alias="responsePath")
```

(Phase 1 already declared `HttpNode` with these fields — verify matches; amend if drift.)

### 6.2 Executor behaviour

```python
# src/executors/http.py  (new)

@register_executor("http")
class HttpExecutor:
    def __init__(self, node: HttpNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        method = (self.node.data.method or "GET").upper()
        url = substitute(self.node.data.url or "", state)
        headers = substitute_in_value(self.node.data.headers or {}, state)
        body_raw = substitute_in_value(self.node.data.body, state)

        # Choose body encoding
        body_kwargs: dict[str, Any] = {}
        if isinstance(body_raw, dict | list):
            body_kwargs["json"] = body_raw
        elif isinstance(body_raw, str) and body_raw:
            body_kwargs["content"] = body_raw

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(method, url, headers=headers, **body_kwargs)

        if resp.status_code >= 400:
            raise HttpNodeError(
                f"http node {self.node.id!r} got HTTP {resp.status_code} from {url!r}: "
                f"{resp.text[:200]}"
            )

        # Parse body
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            parsed: Any = resp.json()
        else:
            parsed = resp.text

        # Optional dot-path extraction
        if self.node.data.response_path:
            parsed = _dot_path(parsed, self.node.data.response_path)

        return _delta(self.node, output=parsed, input={"method": method, "url": url})
```

### 6.3 `_dot_path`

```python
def _dot_path(value: Any, path: str) -> Any:
    """Walk a dot-separated path into a nested structure.  Missing keys → None."""
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
```

No bracket syntax, no wildcards. If a workflow needs more, they chain a `transform` node after `http`.

### 6.4 Error model

`HttpNodeError(RuntimeError)` — single exception class. Raised on:
- Network failure (`httpx.HTTPError` wrapped)
- Non-2xx response
- JSON parse error when `content-type` claims JSON

The Agent executor pattern (catch → `ToolMessage`) doesn't apply — the graph's exception handling in `LangGraphExecutor.run` marks the execution as `failed` and records `error: {type}: {msg}`.

## 7. `set-state` executor

```python
class SetStateNodeData(BaseNodeData):
    state_key: str = Field(alias="stateKey")
    value: Any = None
```

```python
@register_executor("set-state")
class SetStateExecutor:
    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        if not self.node.data.state_key:
            raise ValueError(f"set-state node {self.node.id!r} requires stateKey")
        resolved = substitute_in_value(self.node.data.value, state)
        return {
            "variables": {
                self.node.data.state_key: resolved,
                "lastOutput": resolved,  # so downstream nodes see it without naming it
            },
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {self.node.data.state_key: resolved},
                    "output": resolved,
                }
            },
        }
```

The delta uses the workflow state's `last_wins` reducer on `variables` (Phase 1). Multiple `set-state` nodes in sequence merge cumulatively.

## 8. `transform` executor

```python
class TransformNodeData(BaseNodeData):
    expression: str = ""
```

```python
@register_executor("transform")
class TransformExecutor:
    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        if not self.node.data.expression:
            raise ValueError(f"transform node {self.node.id!r} requires expression")
        try:
            result = evaluate(self.node.data.expression, state)
        except EvalError as exc:
            raise TransformNodeError(
                f"transform node {self.node.id!r} expression failed: {exc}"
            ) from exc
        return _delta(self.node, output=result, input={"expression": self.node.data.expression})
```

**Deliberate OAB deviation:** simpleeval's operator set is `+ - * / ** % < <= > >= == != and or not in` plus `[]` indexing and attribute access on whitelisted names. OAB's JS `vm` supports `?.`, template literals, destructuring, arrow functions. None of those translate. Deviations are documented in the executor's module docstring; real workflows can express equivalent logic in Python expression syntax.

## 9. `data-transform` executor

```python
class DataTransformNodeData(BaseNodeData):
    operation: str = "map"                        # map | filter | reduce
    collection: str = ""                          # templated expression that yields an iterable
    expression: str = ""                          # per-item simpleeval expression
    item_var: str = Field(default="item", alias="itemVar")
    initial: Any = None                           # only used for reduce
```

```python
@register_executor("data-transform")
class DataTransformExecutor:
    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        coll = evaluate(self.node.data.collection, state)
        if not isinstance(coll, list | tuple):
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} collection evaluated to "
                f"non-iterable type {type(coll).__name__}"
            )
        op = self.node.data.operation
        item_var = self.node.data.item_var

        if op == "map":
            out: list[Any] = [
                evaluate(self.node.data.expression, state, extra_names={item_var: x})
                for x in coll
            ]
            return _delta(self.node, output=out, input=_input_kwargs())
        if op == "filter":
            out = [
                x for x in coll
                if evaluate(self.node.data.expression, state, extra_names={item_var: x})
            ]
            return _delta(self.node, output=out, input=_input_kwargs())
        if op == "reduce":
            acc: Any = self.node.data.initial
            for x in coll:
                acc = evaluate(
                    self.node.data.expression,
                    state,
                    extra_names={item_var: x, "acc": acc},
                )
            return _delta(self.node, output=acc, input=_input_kwargs())
        raise DataTransformNodeError(
            f"data-transform node {self.node.id!r} has unsupported operation {op!r}"
        )
```

`reduce` exposes `acc` as an additional scope name. That's the single deviation from the "one item var" rule; documented.

## 10. `extract` executor

```python
class ExtractNodeData(BaseNodeData):
    schema: dict[str, Any] | None = None         # JSON Schema
    input_text: str | None = Field(default=None, alias="input")
    model: str | None = None
```

```python
@register_executor("extract")
class ExtractExecutor:
    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        input_text = substitute(
            self.node.data.input_text or "{{lastOutput}}", state
        )
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
            schema=self.node.data.schema,
            json_mode=self.node.data.schema is None,
        )
        parsed = _coerce_to_dict(result)
        return _delta(self.node, output=parsed, input={"input": input_text[:200]})
```

`DEFAULT_EXTRACT_MODEL = "anthropic/claude-haiku-4-5-20251001"` — same as agent's default.

`_coerce_to_dict` — if `structured_invoke` returned an `AIMessage` (rather than a dict), extract its `content` and `json.loads` it (same pattern as Phase 2 agent's `_format_output`). If even that fails, return the raw string. Document the degradation in the executor's docstring.

## 11. Sentinel test updates

Task 2 ships `http` + moves the sentinel in one commit to avoid cross-task churn.

Currently, three tests use `"http"` + `"Phase 4"`:
- `tests/unit/executors/test_registry.py::test_unshipped_type_raises_with_phase_hint`
- `tests/unit/engine/test_graph_builder.py::test_build_graph_rejects_unshipped_executor_type`
- `tests/unit/engine/test_langgraph_executor.py::test_run_marks_failed_on_exception`

All three change to `"user-approval"` + `"Phase 5"`. The three following executor ships (set-state, transform, data-transform, extract) don't touch sentinels.

## 12. Test plan

### 12.1 Unit tests per executor (TDD shape from Phases 2 + 3)

Each executor gets ~5-8 tests covering: happy path, missing-required-field raises, variable substitution works, error mode (where applicable), edge cases (empty collection for data-transform, non-2xx for http, empty text for extract).

Totals:
- `http`: ~7 tests (GET, POST-with-json-body, POST-with-text-body, non-2xx, responsePath dot-path, header substitution, timeout → error)
- `set-state`: ~4 tests (happy, missing stateKey raises, nested dict value substitution, last-wins reducer behaves)
- `transform`: ~6 tests (arithmetic, string manipulation, variables access, lastOutput shorthand, missing-name raises, dunder access blocked)
- `data-transform`: ~8 tests (map, filter, reduce with initial, empty collection, unknown op raises, item_var override, non-list raises, nested)
- `extract`: ~5 tests (schema-based, json-mode, empty input raises, AIMessage coercion, model override)

Plus:
- `_eval.py`: ~4 tests (builtins blocked, dunder blocked, variables exposed, extra_names merged)
- `variable_substitution.py`: ~3 new tests for `substitute_in_value` (dict recursion, list recursion, leaf passthrough)

Total ~41 new unit tests. Overall target: ~292 tests non-integration after 4a.

### 12.2 Integration

`tests/integration/test_linear_multinode.py` — one workflow:

```
Start → HTTP (GET jsonplaceholder.typicode.com/todos/1)
      → Extract (schema={title: str, completed: bool})
      → Set-State (stateKey="todoSummary", value="{{lastOutput.title}}")
      → End
```

Skips unless `ANTHROPIC_API_KEY` + `TEST_DATABASE_URL` set. Asserts execution `completed`, `variables.todoSummary` is non-empty string.

### 12.3 Regression port

`tests/regression/test_oab_http_transform.py` — port one OAB spec. Likely candidate: OAB's `lib/workflow/nodes/__tests__/http.spec.ts` if it exists (or pick the simplest executor spec OAB has).

## 13. Phase-exit checklist

- [ ] All unit tests green: `ruff`, `pyright --strict`, `pytest -m "not integration"`.
- [ ] Integration test passes against real Neon + real Anthropic.
- [ ] OAB regression port passes.
- [ ] Sentinel tests use `"user-approval"` + `"Phase 5"`.
- [ ] Five executors in `_REGISTRY`; only `if-else` + `while` still raise NotImplementedError for Phase 4.
- [ ] CHANGELOG Phase 4a section with scorecard.
- [ ] CLAUDE.md phase table: 4a ✅ Complete, 4b ⏭ Next.
- [ ] ADR-0012 `Implemented by` backfilled.

## 14. Risks and mitigations

**Risk 1.** simpleeval's operator set doesn't cover a workflow user's expectation (e.g., they want `?.`). **Mitigation:** clear error message via `EvalError`; document in `_eval.py` docstring that Composer uses Python expression syntax not JS.

**Risk 2.** `http` node hits a real redirect chain, runs 60s, holds a test process open. **Mitigation:** explicit 60s timeout; integration test uses known-fast endpoint (jsonplaceholder); retries not in scope.

**Risk 3.** `extract` returns a string rather than a dict when the LLM ignores the schema. **Mitigation:** `_coerce_to_dict` handles AIMessage→str→json.loads fallback; the executor's `input` in `node_results` is truncated to 200 chars to keep audit rows small.

**Risk 4.** `data-transform` over a large collection (10k+ items) invokes simpleeval per element and blocks the event loop. **Mitigation:** out of scope for 4a; flag in the executor's docstring. Phase 9 (hardening) can introduce chunking / timeouts.

**Risk 5.** The dict/list recursive substitution creates infinite recursion if a dict references itself. **Mitigation:** Python's default recursion limit catches it with a `RecursionError`. We don't add a cycle detector; cycles in workflow-authored state are the author's bug, not the engine's.

## 15. New ADR

### ADR-0012: simpleeval is the only eval primitive

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Four Phase 4 executors (`transform`, `data-transform`, plus 4b's `if-else` and `while`) evaluate user-supplied expressions over workflow state. Three options considered:

1. **Python `eval()` / `exec()`.** Most expressive, no deps. But: full access to `__builtins__`, can read/write files, import modules, escape sandbox. Catastrophic attack surface for a tool where users can author arbitrary expression strings.
2. **Custom parser / AST walk.** Full control, but a non-trivial build-and-maintain commitment for a secondary feature.
3. **`simpleeval`.** Small library (~400 LOC). Explicit whitelist of names and functions; blocks `__` attribute access by default; supports Python expression syntax people already know.

**Decision.** `simpleeval`, exclusively. One wrapper lives at `src/executors/_eval.py` and every expression-eval path in the codebase routes through it. `eval()` / `exec()` are forbidden in production code. CI's ruff configuration includes `S307` (use of `eval`) as an error.

**Scope bound.** The simpleeval scope exposes: `variables`, `lastOutput`, `node_results`, and a per-call `extra_names` dict (e.g., `item` for `data-transform` iteration, `acc` for `reduce`). No builtins, no imports, no dunder access.

**Alternatives considered** (beyond the three above):

- **asteval.** Similar to simpleeval but with numpy-friendly semantics. Not required for composer workloads; extra surface area.
- **JavaScript sandbox via py-mini-racer.** Reintroduces the attack surface of a full JS runtime; build-time native deps. Worth it if OAB parity demanded JS-identical semantics, but behavioural parity (the actual test oracle) doesn't require syntactic parity.

**Consequences.**

- Users migrating workflows from OAB will hit syntax differences (no arrow functions, no template literals, no `?.`). The error message from `EvalError` names the expression and the underlying cause, so translation is mechanical.
- Adding a new operator / function to the scope means editing `_eval.py` once. No per-executor drift.
- CI gate: any new call to `eval(`, `exec(`, or `compile(` in `src/` fails lint. Documented in `pyproject.toml`.

**Implemented by.** Phase 4a (commits TBD).

**Related.** ADR-0002 (workflow schema — `transform`, `data-transform`, `if-else`, `while` all accept an expression field), CLAUDE.md §Conventions ("NEVER `eval()`; use `simpleeval`").

## 16. Execution handoff

After this spec is approved and committed, the plan is written to `docs/superpowers/plans/2026-04-21-phase-4a-linear-executors-plan.md` and executed via `superpowers:subagent-driven-development`, same cadence as Phases 1, 2, 3a, 3b. ~10 tasks; one commit per task.
