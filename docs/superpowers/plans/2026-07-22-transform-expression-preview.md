# Transform Expression Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let designers test a `transform`/`data-transform` node's simpleeval expression against sample state directly in the node panel, before saving, without running a full workflow execution.

**Architecture:** Two new stateless FastAPI endpoints (`POST /expressions/evaluate-transform`, `POST /expressions/evaluate-data-transform`) wrap the existing `evaluate()` primitive and a newly-extracted `run_map_filter_reduce()` helper (shared with the real `DataTransformExecutor`, so preview and real execution can never drift). The frontend adds a "Test expression" section to both node panels, sourcing sample state from the workflow's most recent execution (editable) via a new shared `sample-state-field.tsx` component.

**Tech Stack:** FastAPI, Pydantic v2, simpleeval (via `src/executors/_eval.py`), pytest, Next.js/React, `@tanstack/react-query`, vitest.

**Spec:** `docs/superpowers/specs/2026-07-22-transform-expression-preview-design.md`

---

## Pre-existing bug found during planning

While tracing how the frontend would fetch "the workflow's most recent execution," `frontend/lib/api/executions.ts`'s `listExecutions()` was found to send the query parameter as `workflow_id` (snake_case), but the backend route (`src/api/executions.py`) declares `Query(default=None, alias="workflowId")` — it only binds `workflowId` (camelCase). No existing caller passes `workflowId` today (checked via grep), so this bug is currently dormant — but this plan's sample-state feature would be the first real caller, and would silently show the wrong workflow's data if left unfixed. Task 6 fixes it with a regression test before anything is built on top of it.

---

### Task 1: Add rate-limit config setting

**Files:**
- Modify: `src/config.py:274`

- [ ] **Step 1: Add the new setting**

In `src/config.py`, right after the `rate_limit_mcp_test_per_minute` line (line 274):

```python
    rate_limit_mcp_test_per_minute: int = 10
    rate_limit_expression_test_per_minute: int = 30
```

- [ ] **Step 2: Verify it loads**

Run: `.venv/Scripts/python.exe -c "from src.config import get_settings; print(get_settings().rate_limit_expression_test_per_minute)"`
Expected: `30`

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "feat: add rate limit config for expression-test endpoints"
```

---

### Task 2: Extract shared map/filter/reduce loop in data_transform.py

**Files:**
- Modify: `src/executors/data_transform.py`
- Modify: `tests/unit/executors/test_data_transform_executor.py`

The evaluate-data-transform preview endpoint (Task 4) needs to run the exact same map/filter/reduce loop `DataTransformExecutor` uses in real execution — otherwise the preview could show a different result than a real run. Extract the loop into a standalone function both can call, and make the operation-name set public so the endpoint can validate against the same source of truth.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/executors/test_data_transform_executor.py` (after the existing imports, before `_dt_node`):

```python
from src.engine.state import initial_state as _initial_state_for_helper
from src.executors.data_transform import SUPPORTED_OPS, run_map_filter_reduce
```

Add these test functions at the end of the file:

```python
def test_supported_ops_is_public() -> None:
    assert SUPPORTED_OPS == {"map", "filter", "reduce"}


async def test_run_map_filter_reduce_map() -> None:
    state = _initial_state_for_helper()
    result = run_map_filter_reduce("map", [1, 2, 3], "item * 2", "item", None, state)
    assert result == [2, 4, 6]


async def test_run_map_filter_reduce_filter() -> None:
    state = _initial_state_for_helper()
    result = run_map_filter_reduce("filter", [-1, 0, 1, 2], "item > 0", "item", None, state)
    assert result == [1, 2]


async def test_run_map_filter_reduce_reduce() -> None:
    state = _initial_state_for_helper()
    result = run_map_filter_reduce("reduce", [1, 2, 3, 4], "acc + item", "item", 0, state)
    assert result == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_data_transform_executor.py -v --no-cov`
Expected: FAIL — `ImportError: cannot import name 'SUPPORTED_OPS' from 'src.executors.data_transform'` (and `run_map_filter_reduce`)

- [ ] **Step 3: Extract the helper and rename the ops set**

In `src/executors/data_transform.py`, replace lines 22 (`_SUPPORTED_OPS = {"map", "filter", "reduce"}`) through the end of the file's `arun` loop (lines 58-76) and `__all__` (line 96) as follows.

Replace:
```python
_SUPPORTED_OPS = {"map", "filter", "reduce"}
```
with:
```python
SUPPORTED_OPS = {"map", "filter", "reduce"}


def run_map_filter_reduce(
    op: str,
    coll: list[Any] | tuple[Any, ...],
    expr: str,
    item_var: str,
    initial: Any,
    state: WorkflowStateDict,
) -> Any:
    """Run map/filter/reduce over `coll`, evaluating `expr` per item.

    Shared by DataTransformExecutor (real execution) and the
    evaluate-data-transform test-preview endpoint (src/api/expressions.py),
    so the preview can never drift from real execution behavior. Assumes
    `op` is already validated to be one of SUPPORTED_OPS. Raises EvalError
    on any per-item evaluation failure.
    """
    if op == "map":
        return [evaluate(expr, state, extra_names={item_var: x}) for x in coll]
    if op == "filter":
        return [x for x in coll if evaluate(expr, state, extra_names={item_var: x})]
    # reduce
    acc: Any = initial
    for x in coll:
        acc = evaluate(expr, state, extra_names={item_var: x, "acc": acc})
    return acc
```

Replace the `op not in _SUPPORTED_OPS` check inside `arun`:
```python
        op = self.node.data.operation
        if op not in _SUPPORTED_OPS:
```
with:
```python
        op = self.node.data.operation
        if op not in SUPPORTED_OPS:
```

Replace the inline loop inside `arun`:
```python
        output: Any
        try:
            if op == "map":
                output = [evaluate(expr, state, extra_names={item_var: x}) for x in coll]
            elif op == "filter":
                output = [x for x in coll if evaluate(expr, state, extra_names={item_var: x})]
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
```
with:
```python
        try:
            output = run_map_filter_reduce(op, coll, expr, item_var, self.node.data.initial, state)
        except EvalError as exc:
            raise DataTransformNodeError(
                f"data-transform node {self.node.id!r} per-item expression: {exc}"
            ) from exc
```

Replace the final `__all__`:
```python
__all__ = ["DataTransformExecutor", "DataTransformNodeError"]
```
with:
```python
__all__ = [
    "DataTransformExecutor",
    "DataTransformNodeError",
    "SUPPORTED_OPS",
    "run_map_filter_reduce",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/executors/test_data_transform_executor.py -v --no-cov`
Expected: all tests pass, including the 4 new ones and all pre-existing ones (`test_map_doubles_items`, `test_filter_keeps_positives`, `test_reduce_sums_with_initial`, `test_item_var_override`, `test_empty_collection_returns_empty_for_map`, `test_non_iterable_collection_raises`, `test_unknown_operation_raises`, `test_data_transform_executor_is_registered`) — this refactor must not change any existing behavior.

- [ ] **Step 5: Lint and typecheck**

Run: `.venv/Scripts/python.exe -m ruff check src/executors/data_transform.py tests/unit/executors/test_data_transform_executor.py && .venv/Scripts/python.exe -m ruff format --check src/executors/data_transform.py tests/unit/executors/test_data_transform_executor.py`
Expected: no errors. If ruff format reports unformatted files, run `.venv/Scripts/python.exe -m ruff format src/executors/data_transform.py tests/unit/executors/test_data_transform_executor.py` and re-check.

Run: `.venv/Scripts/python.exe -m pyright src/executors/data_transform.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/executors/data_transform.py tests/unit/executors/test_data_transform_executor.py
git commit -m "refactor: extract run_map_filter_reduce from DataTransformExecutor

Shared by the real executor and the upcoming evaluate-data-transform
preview endpoint, so a passing preview test can never drift from what
a real execution actually does."
```

---

### Task 3: Backend endpoint — POST /expressions/evaluate-transform

**Files:**
- Create: `src/api/expressions.py`
- Create: `tests/unit/api/test_expressions.py`
- Modify: `src/main.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_expressions.py`:

```python
"""Tests for /expressions test/preview endpoints."""

from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _client() -> TestClient:
    app = create_app()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def test_evaluate_transform_success() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "lastOutput.upper()", "variables": {"lastOutput": "hi"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "result": "HI", "error": None}


def test_evaluate_transform_undefined_name_returns_ok_false() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "not_a_real_name + 1", "variables": {}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "not_a_real_name" in body["error"]


def test_evaluate_transform_reads_top_level_variable() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-transform",
        json={"expression": "counter + 1", "variables": {"counter": 41}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "result": 42, "error": None}


def test_evaluate_transform_defaults_variables_to_empty_dict() -> None:
    client = _client()
    resp = client.post("/expressions/evaluate-transform", json={"expression": "1 + 1"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "result": 2, "error": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_expressions.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.expressions'` (route doesn't exist yet, so `create_app()` itself still works, but the POST returns 404).

- [ ] **Step 3: Create the router**

Create `src/api/expressions.py`:

```python
"""Expression test/preview endpoints for the Designer's transform panels.

Lets a designer try a simpleeval expression against sample state before
saving a transform / data-transform node, instead of only discovering a
typo or wrong variable reference via a full workflow execution. Both
endpoints are pure request/response: no workflow lookup, no DB write, no
execution created. They route through the exact same `evaluate()` real
executions use (ADR-0012 -- evaluate() is the only eval primitive in
Composer), so a passing test here is a real guarantee, not a guess.

See docs/superpowers/specs/2026-07-22-transform-expression-preview-design.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.engine.state import initial_state
from src.executors._eval import EvalError, evaluate
from src.security.auth import get_current_user_id
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)

router = APIRouter(tags=["expressions"])


class EvaluateTransformRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expression: str
    variables: dict[str, Any] = Field(default_factory=dict)


class EvaluateExpressionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    result: Any = None
    error: str | None = None


@router.post("/expressions/evaluate-transform", response_model=EvaluateExpressionResult)
async def evaluate_transform_expression(
    payload: EvaluateTransformRequest,
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> EvaluateExpressionResult:  # pyright: ignore[reportUnusedFunction]
    await enforce(
        limiter,
        route_key="expression_test",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_expression_test_per_minute),
    )
    state = initial_state()
    state["variables"] = payload.variables
    try:
        result = evaluate(payload.expression, state)
    except EvalError as exc:
        return EvaluateExpressionResult(ok=False, error=str(exc))
    return EvaluateExpressionResult(ok=True, result=result)


__all__ = [
    "EvaluateExpressionResult",
    "EvaluateTransformRequest",
    "router",
]
```

- [ ] **Step 4: Register the router**

In `src/main.py`, add the import alongside the other `src.api.*` imports (alphabetically, after `events_ws_router` and before `internal_router` — line 22 area):

```python
from src.api.events_ws import router as events_ws_router
from src.api.executions import router as executions_router
from src.api.expressions import router as expressions_router
from src.api.internal import router as internal_router
```

And add `app.include_router(expressions_router)` alongside the others (after `app.include_router(executions_router)`):

```python
    app.include_router(executions_router)
    app.include_router(expressions_router)
    app.include_router(events_ws_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_expressions.py -v --no-cov`
Expected: 4 passed.

- [ ] **Step 6: Lint and typecheck**

Run: `.venv/Scripts/python.exe -m ruff check src/api/expressions.py src/main.py tests/unit/api/test_expressions.py && .venv/Scripts/python.exe -m ruff format --check src/api/expressions.py src/main.py tests/unit/api/test_expressions.py`
Run: `.venv/Scripts/python.exe -m pyright src/api/expressions.py src/main.py`
Expected: no errors in either.

- [ ] **Step 7: Commit**

```bash
git add src/api/expressions.py src/main.py tests/unit/api/test_expressions.py
git commit -m "feat: add POST /expressions/evaluate-transform preview endpoint"
```

---

### Task 4: Backend endpoint — POST /expressions/evaluate-data-transform

**Files:**
- Modify: `src/api/expressions.py`
- Modify: `tests/unit/api/test_expressions.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/api/test_expressions.py`:

```python
def test_evaluate_data_transform_map() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['nums']",
            "expression": "item * 2",
            "itemVar": "item",
            "variables": {"nums": [1, 2, 3]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == [2, 4, 6]
    assert body["itemCount"] == 3
    assert body["truncated"] is False


def test_evaluate_data_transform_reduce_with_initial() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "reduce",
            "collection": "variables['nums']",
            "expression": "acc + item",
            "itemVar": "item",
            "initial": 0,
            "variables": {"nums": [1, 2, 3, 4]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "result": 10, "error": None, "itemCount": 4, "truncated": False}


def test_evaluate_data_transform_non_list_collection() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['scalar']",
            "expression": "item",
            "itemVar": "item",
            "variables": {"scalar": 42},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "non-iterable" in body["error"] or "int" in body["error"]


def test_evaluate_data_transform_unknown_operation() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "sum",
            "collection": "variables['nums']",
            "expression": "item",
            "itemVar": "item",
            "variables": {"nums": [1, 2]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "sum" in body["error"]


def test_evaluate_data_transform_truncates_at_50_items() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['nums']",
            "expression": "item",
            "itemVar": "item",
            "variables": {"nums": list(range(60))},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["itemCount"] == 50
    assert body["truncated"] is True
    assert len(body["result"]) == 50


def test_evaluate_data_transform_bad_per_item_expression() -> None:
    client = _client()
    resp = client.post(
        "/expressions/evaluate-data-transform",
        json={
            "operation": "map",
            "collection": "variables['nums']",
            "expression": "item.nonexistent_attr",
            "itemVar": "item",
            "variables": {"nums": [1, 2]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_expressions.py -v --no-cov -k data_transform`
Expected: FAIL — 404, since the route doesn't exist yet.

- [ ] **Step 3: Implement the endpoint**

In `src/api/expressions.py`, change the import block:

```python
from src.executors._eval import EvalError, evaluate
```
to:
```python
from src.executors._eval import EvalError, evaluate
from src.executors.data_transform import SUPPORTED_OPS, run_map_filter_reduce
```

Add after `EvaluateExpressionResult`'s class body, before the `@router.post("/expressions/evaluate-transform"...)` endpoint:

```python
# Defensive cap on evaluate-data-transform's collection size. This
# endpoint accepts a client-supplied collection directly -- unlike real
# execution, no workflow input-size limit applies here, so unbounded
# input from an authenticated-but-adversarial client shouldn't cost the
# server unbounded eval time.
_MAX_TEST_ITEMS = 50


class EvaluateDataTransformRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation: str
    collection: str
    expression: str
    item_var: str = Field(default="item", alias="itemVar")
    initial: Any = None
    variables: dict[str, Any] = Field(default_factory=dict)


class EvaluateDataTransformResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    result: Any = None
    error: str | None = None
    item_count: int | None = Field(default=None, alias="itemCount")
    truncated: bool = False
```

Add after the `evaluate_transform_expression` endpoint function, before `__all__`:

```python
@router.post(
    "/expressions/evaluate-data-transform", response_model=EvaluateDataTransformResult
)
async def evaluate_data_transform_expression(
    payload: EvaluateDataTransformRequest,
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> EvaluateDataTransformResult:  # pyright: ignore[reportUnusedFunction]
    await enforce(
        limiter,
        route_key="expression_test",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_expression_test_per_minute),
    )
    if payload.operation not in SUPPORTED_OPS:
        return EvaluateDataTransformResult(
            ok=False,
            error=(
                f"operation {payload.operation!r} not supported "
                "(need map / filter / reduce)"
            ),
        )

    state = initial_state()
    state["variables"] = payload.variables

    try:
        coll = evaluate(payload.collection, state)
    except EvalError as exc:
        return EvaluateDataTransformResult(ok=False, error=f"collection: {exc}")

    if not isinstance(coll, (list, tuple)):
        return EvaluateDataTransformResult(
            ok=False,
            error=(
                f"collection evaluated to non-iterable type {type(coll).__name__}, "
                "expected a list"
            ),
        )

    truncated = len(coll) > _MAX_TEST_ITEMS
    sample = list(coll[:_MAX_TEST_ITEMS])

    try:
        result = run_map_filter_reduce(
            payload.operation, sample, payload.expression, payload.item_var, payload.initial, state
        )
    except EvalError as exc:
        return EvaluateDataTransformResult(ok=False, error=f"per-item expression: {exc}")

    return EvaluateDataTransformResult(
        ok=True, result=result, item_count=len(sample), truncated=truncated
    )
```

Update `__all__`:
```python
__all__ = [
    "EvaluateDataTransformRequest",
    "EvaluateDataTransformResult",
    "EvaluateExpressionResult",
    "EvaluateTransformRequest",
    "router",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_expressions.py -v --no-cov`
Expected: all 10 tests pass (4 from Task 3 + 6 new).

- [ ] **Step 5: Lint and typecheck**

Run: `.venv/Scripts/python.exe -m ruff check src/api/expressions.py tests/unit/api/test_expressions.py && .venv/Scripts/python.exe -m ruff format --check src/api/expressions.py tests/unit/api/test_expressions.py`
Run: `.venv/Scripts/python.exe -m pyright src/api/expressions.py`
Expected: no errors.

- [ ] **Step 6: Run the full backend suite**

Run: `.venv/Scripts/python.exe -m pytest -m "not integration" --no-cov -q`
Expected: all tests pass, count increased by 14 (4 data_transform executor tests + 10 expressions endpoint tests) over the pre-existing 1230.

- [ ] **Step 7: Commit**

```bash
git add src/api/expressions.py tests/unit/api/test_expressions.py
git commit -m "feat: add POST /expressions/evaluate-data-transform preview endpoint

Runs the real map/filter/reduce loop (via the shared
run_map_filter_reduce helper) over up to 50 sample items, so the
preview shows an actual end-to-end result -- not just a single item --
for reduce in particular, where a one-item preview would be
meaningless."
```

---

### Task 5: Regenerate the frontend OpenAPI client

**Files:**
- Modify: `frontend/lib/api/generated/schema.ts` (auto-generated, do not hand-edit)

- [ ] **Step 1: Start the backend**

Run in a background terminal: `.venv/Scripts/python.exe -m uvicorn src.main:app --port 8000`
Expected: server starts, logs "Uvicorn running on http://0.0.0.0:8000".

- [ ] **Step 2: Regenerate the client**

Run (from `frontend/`): `npm run generate-client`
Expected: `frontend/lib/api/generated/schema.ts` is rewritten and now contains `EvaluateTransformRequest`, `EvaluateExpressionResult`, `EvaluateDataTransformRequest`, `EvaluateDataTransformResult` schema types.

Verify: `grep -c "EvaluateDataTransformResult" frontend/lib/api/generated/schema.ts` — expect a non-zero count.

- [ ] **Step 3: Stop the backend**

Stop the uvicorn process started in Step 1.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/api/generated/schema.ts
git commit -m "chore: regenerate OpenAPI client for expression-test endpoints"
```

---

### Task 6: Fix listExecutions' workflowId query-param bug

**Files:**
- Modify: `frontend/lib/api/executions.ts:14`
- Create: `frontend/lib/api/executions.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/lib/api/executions.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("./client", () => ({ apiFetch }));

import { listExecutions } from "./executions";

beforeEach(() => {
  vi.clearAllMocks();
  apiFetch.mockResolvedValue({ total: 0, items: [], limit: 50, offset: 0 });
});

describe("listExecutions", () => {
  it("sends workflowId as the query key the backend actually binds", async () => {
    await listExecutions({ workflowId: "wf-1", limit: 1 });
    expect(apiFetch).toHaveBeenCalledWith("/executions?workflowId=wf-1&limit=1");
  });

  it("omits the query string entirely when called with no params", async () => {
    await listExecutions();
    expect(apiFetch).toHaveBeenCalledWith("/executions");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npm test -- executions.test.ts`
Expected: FAIL — first test's actual call was `/executions?workflow_id=wf-1&limit=1` (wrong key), not `workflowId=wf-1`.

- [ ] **Step 3: Fix the bug**

In `frontend/lib/api/executions.ts`, line 14:

Replace:
```ts
  if (params?.workflowId) q.set("workflow_id", params.workflowId);
```
with:
```ts
  if (params?.workflowId) q.set("workflowId", params.workflowId);
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npm test -- executions.test.ts`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/executions.ts frontend/lib/api/executions.test.ts
git commit -m "fix: listExecutions sent workflow_id instead of the workflowId the backend binds

Backend declares Query(alias=\"workflowId\") -- the snake_case key was
silently ignored, so any caller filtering by workflow got back the
user's most recent executions across ALL workflows instead. No
existing caller passed workflowId (checked via grep), so this was
dormant until now; the transform-expression-preview feature is the
first real caller and would have silently shown the wrong workflow's
sample data without this fix."
```

---

### Task 7: Frontend API client for the new endpoints

**Files:**
- Create: `frontend/lib/api/expressions.ts`

- [ ] **Step 1: Create the client functions**

```ts
import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type EvaluateTransformRequest = components["schemas"]["EvaluateTransformRequest"];
type EvaluateExpressionResult = components["schemas"]["EvaluateExpressionResult"];
type EvaluateDataTransformRequest = components["schemas"]["EvaluateDataTransformRequest"];
type EvaluateDataTransformResult = components["schemas"]["EvaluateDataTransformResult"];

export async function evaluateTransformExpression(
  body: EvaluateTransformRequest
): Promise<EvaluateExpressionResult> {
  return apiFetch<EvaluateExpressionResult>("/expressions/evaluate-transform", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function evaluateDataTransformExpression(
  body: EvaluateDataTransformRequest
): Promise<EvaluateDataTransformResult> {
  return apiFetch<EvaluateDataTransformResult>("/expressions/evaluate-data-transform", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors referencing `expressions.ts` (the generated schema types from Task 5 must already contain these four schema names, or this fails).

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/api/expressions.ts
git commit -m "feat: add frontend API client for expression-test endpoints"
```

---

### Task 8: Thread workflowId into node panels

**Files:**
- Modify: `frontend/app/designer/[workflowId]/page.tsx:327`
- Modify: `frontend/components/composer/canvas/workflow-canvas.tsx`
- Modify: `frontend/components/composer/canvas/property-panel.tsx`

Node panels currently receive `data`, `onChange`, `allNodes`, `currentNodeId` — but not the workflow's own id, which the sample-state feature needs to fetch "this workflow's most recent execution." Thread it down through the existing prop chain.

- [ ] **Step 1: Pass workflowId from the designer page**

In `frontend/app/designer/[workflowId]/page.tsx`, the `<WorkflowCanvas` call starting at line 327:

Replace:
```tsx
          <WorkflowCanvas
            initialNodes={rfNodes}
            initialEdges={rfEdges}
```
with:
```tsx
          <WorkflowCanvas
            workflowId={workflowId}
            initialNodes={rfNodes}
            initialEdges={rfEdges}
```

- [ ] **Step 2: Accept and forward it in WorkflowCanvas**

In `frontend/components/composer/canvas/workflow-canvas.tsx`, add to `WorkflowCanvasProps` (after the `runState` field):

```tsx
interface WorkflowCanvasProps {
  initialNodes: RFNode[];
  initialEdges: RFEdge[];
  onNodesChange?: (nodes: RFNode[]) => void;
  onEdgesChange?: (edges: RFEdge[]) => void;
  runState?: DesignerExecutionState;
  /** The workflow's own id -- threaded down to node panels so features
   *  like the transform "Test expression" section can fetch this
   *  workflow's own execution history. */
  workflowId?: string;
}
```

Destructure it in the function signature:
```tsx
export function WorkflowCanvas({
  initialNodes,
  initialEdges,
  onNodesChange,
  onEdgesChange,
  runState,
  workflowId,
}: WorkflowCanvasProps) {
```

Pass it to `<PropertyPanel>` (around line 599):
```tsx
      {selectedNode && (
        <PropertyPanel
          node={selectedNode}
          allNodes={nodes}
          onChange={handlePanelChange}
          onClose={() => setSelectedNodeId(null)}
          workflowId={workflowId}
        />
      )}
```

- [ ] **Step 3: Accept and forward it in PropertyPanel**

In `frontend/components/composer/canvas/property-panel.tsx`, add to the `PanelProps` type:

```tsx
export type PanelProps = {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  allNodes?: RFNode[];
  currentNodeId?: string;
  /** The workflow's own id -- not every panel needs it, so it's optional
   *  like allNodes/currentNodeId. */
  workflowId?: string;
};
```

Add to `PropertyPanelProps`:
```tsx
interface PropertyPanelProps {
  node: RFNode;
  allNodes: RFNode[];
  onChange: (patch: Record<string, unknown>) => void;
  onClose: () => void;
  workflowId?: string;
}
```

Destructure it and pass it through:
```tsx
export function PropertyPanel({ node, allNodes, onChange, onClose, workflowId }: PropertyPanelProps) {
```

```tsx
        {PanelContent ? (
          <PanelContent
            data={nodeData}
            onChange={onChange}
            allNodes={allNodes}
            currentNodeId={node.id}
            workflowId={workflowId}
          />
        ) : (
```

- [ ] **Step 4: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors. (Existing panels ignore the extra `workflowId` prop harmlessly, same as they already ignore `allNodes`/`currentNodeId` when unused.)

- [ ] **Step 5: Run the frontend test suite**

Run (from `frontend/`): `npm test`
Expected: all existing tests still pass — this task only adds an optional prop, no existing panel's behavior changes.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/designer/\[workflowId\]/page.tsx frontend/components/composer/canvas/workflow-canvas.tsx frontend/components/composer/canvas/property-panel.tsx
git commit -m "feat: thread workflowId prop down to node panels

Needed by the transform/data-transform panels' upcoming 'Test
expression' section to fetch the workflow's own execution history."
```

---

### Task 9: Shared sample-state field component

**Files:**
- Create: `frontend/components/composer/canvas/node-panels/sample-state-field.tsx`
- Create: `frontend/components/composer/canvas/node-panels/sample-state-field.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/components/composer/canvas/node-panels/sample-state-field.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SampleStateField, useSampleState } from "./sample-state-field";

const { listExecutions } = vi.hoisted(() => ({ listExecutions: vi.fn() }));
vi.mock("@/lib/api/executions", () => ({ listExecutions }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useSampleState", () => {
  it("pre-fills from the workflow's most recent execution", async () => {
    listExecutions.mockResolvedValue({
      total: 1,
      items: [{ id: "e1", variables: { counter: 3 }, nodeResults: {} }],
      limit: 1,
      offset: 0,
    });
    const { result } = renderHook(() => useSampleState("wf-1"), { wrapper: wrap });
    await waitFor(() => expect(result.current.text).toContain("counter"));
    expect(result.current.parsed).toEqual({ counter: 3 });
    expect(result.current.error).toBeNull();
  });

  it("starts as an empty object when the workflow has no executions", async () => {
    listExecutions.mockResolvedValue({ total: 0, items: [], limit: 1, offset: 0 });
    const { result } = renderHook(() => useSampleState("wf-1"), { wrapper: wrap });
    await waitFor(() => expect(listExecutions).toHaveBeenCalled());
    expect(result.current.parsed).toEqual({});
  });

  it("does not fetch when workflowId is undefined", () => {
    renderHook(() => useSampleState(undefined), { wrapper: wrap });
    expect(listExecutions).not.toHaveBeenCalled();
  });
});

describe("SampleStateField", () => {
  it("shows an error message when the text is invalid JSON", () => {
    render(
      wrap(<SampleStateField text="{not json" onChangeText={vi.fn()} error="Invalid JSON" />)
    );
    expect(screen.getByText("Invalid JSON")).toBeInTheDocument();
  });

  it("calls onChangeText as the textarea is edited", () => {
    const onChangeText = vi.fn();
    render(wrap(<SampleStateField text="{}" onChangeText={onChangeText} error={null} />));
    screen.getByLabelText(/Sample state/i);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- sample-state-field.test.tsx`
Expected: FAIL — module `./sample-state-field` doesn't exist yet.

- [ ] **Step 3: Implement the component**

Create `frontend/components/composer/canvas/node-panels/sample-state-field.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { listExecutions } from "@/lib/api/executions";

export interface SampleState {
  text: string;
  setText: (text: string) => void;
  parsed: Record<string, unknown> | null;
  error: string | null;
}

/**
 * Sample-state text, pre-filled once from the workflow's most recent
 * execution's variables (if any exist), then fully editable. Parses on
 * every change so callers can gate their "Test" button on validity.
 */
export function useSampleState(workflowId?: string): SampleState {
  const [text, setText] = useState("{}");
  const prefilled = useRef(false);

  const { data } = useQuery({
    queryKey: ["latest-execution-variables", workflowId],
    queryFn: () => listExecutions({ workflowId, limit: 1 }),
    enabled: Boolean(workflowId) && !prefilled.current,
  });

  useEffect(() => {
    if (prefilled.current || !data) return;
    prefilled.current = true;
    const latest = data.items[0];
    const variables = latest?.variables as Record<string, unknown> | undefined;
    if (variables && Object.keys(variables).length > 0) {
      setText(JSON.stringify(variables, null, 2));
    }
  }, [data]);

  let parsed: Record<string, unknown> | null = null;
  let error: string | null = null;
  try {
    const value: unknown = JSON.parse(text);
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      parsed = value as Record<string, unknown>;
    } else {
      error = "Sample state must be a JSON object";
    }
  } catch {
    error = "Invalid JSON";
  }

  return { text, setText, parsed, error };
}

export function SampleStateField({
  text,
  onChangeText,
  error,
}: {
  text: string;
  onChangeText: (text: string) => void;
  error: string | null;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor="sample-state">Sample state (variables)</Label>
      <Textarea
        id="sample-state"
        value={text}
        onChange={(e) => onChangeText(e.target.value)}
        rows={4}
        className="font-mono text-xs"
        aria-invalid={error !== null}
      />
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Pre-filled from the workflow&apos;s most recent execution, if any. Edit
          freely before testing.
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- sample-state-field.test.tsx`
Expected: 5 passed.

- [ ] **Step 5: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/sample-state-field.tsx frontend/components/composer/canvas/node-panels/sample-state-field.test.tsx
git commit -m "feat: add shared sample-state field for expression-test panels"
```

---

### Task 10: transform.tsx — Test expression section

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/transform.tsx`
- Create: `frontend/components/composer/canvas/node-panels/transform.test.tsx`

`transform.tsx` currently has no dedicated test file. This task creates one covering both the pre-existing fields and the new section, matching the convention every other panel in this directory already follows.

- [ ] **Step 1: Write the failing tests**

Create `frontend/components/composer/canvas/node-panels/transform.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TransformPanel from "./transform";

const { listExecutions } = vi.hoisted(() => ({ listExecutions: vi.fn() }));
vi.mock("@/lib/api/executions", () => ({ listExecutions }));

const { evaluateTransformExpression } = vi.hoisted(() => ({
  evaluateTransformExpression: vi.fn(),
}));
vi.mock("@/lib/api/expressions", () => ({ evaluateTransformExpression }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  listExecutions.mockResolvedValue({ total: 0, items: [], limit: 1, offset: 0 });
});

describe("TransformPanel", () => {
  it("editing the expression calls onChange with transformScript", () => {
    const onChange = vi.fn();
    render(wrap(<TransformPanel data={{}} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Expression"), {
      target: { value: "lastOutput.upper()" },
    });
    expect(onChange).toHaveBeenCalledWith({ transformScript: "lastOutput.upper()" });
  });

  it("Test button is disabled when the expression is empty", () => {
    render(wrap(<TransformPanel data={{}} onChange={vi.fn()} />));
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();
  });

  it("runs the test and shows the result on success", async () => {
    evaluateTransformExpression.mockResolvedValue({ ok: true, result: "HI", error: null });
    render(
      wrap(
        <TransformPanel
          data={{ transformScript: "lastOutput.upper()" }}
          onChange={vi.fn()}
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/"HI"|HI/)).toBeInTheDocument());
    expect(evaluateTransformExpression).toHaveBeenCalledWith({
      expression: "lastOutput.upper()",
      variables: {},
    });
  });

  it("shows the error message on a failed test", async () => {
    evaluateTransformExpression.mockResolvedValue({
      ok: false,
      result: null,
      error: "simpleeval failed evaluating 'bad': NameNotDefined: bad is not defined",
    });
    render(
      wrap(<TransformPanel data={{ transformScript: "bad" }} onChange={vi.fn()} workflowId="wf-1" />)
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() =>
      expect(screen.getByText(/NameNotDefined/)).toBeInTheDocument()
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- transform.test.tsx`
Expected: FAIL — no "Test" button exists yet in `transform.tsx`.

- [ ] **Step 3: Add the Test expression section**

In `frontend/components/composer/canvas/node-panels/transform.tsx`, replace the imports:

```tsx
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
```
with:
```tsx
import { useMutation } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { evaluateTransformExpression } from "@/lib/api/expressions";
import { SampleStateField, useSampleState } from "./sample-state-field";
```

Replace the component signature and body opening:
```tsx
export default function TransformPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const transformScript = (data.transformScript as string) ?? "";
  const outputKey = (data.outputKey as string) ?? "";
  const outputKeyError = describeOutputKeyError(outputKey);

  return (
```
with:
```tsx
export default function TransformPanel({
  data,
  onChange,
  workflowId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  workflowId?: string;
}) {
  const transformScript = (data.transformScript as string) ?? "";
  const outputKey = (data.outputKey as string) ?? "";
  const outputKeyError = describeOutputKeyError(outputKey);

  const sampleState = useSampleState(workflowId);
  const testMutation = useMutation({
    mutationFn: () =>
      evaluateTransformExpression({
        expression: transformScript,
        variables: sampleState.parsed ?? {},
      }),
  });

  return (
```

Add the new section right before the closing `</div>` at the end of the component's returned JSX (after the existing "Output variable name" field's closing `</div>`, still inside the outer `<div className="space-y-4">`):

```tsx
      <div className="space-y-2 border-t pt-4">
        <Label>Test expression</Label>
        <SampleStateField
          text={sampleState.text}
          onChangeText={sampleState.setText}
          error={sampleState.error}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!transformScript || sampleState.error !== null || testMutation.isPending}
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending ? "Testing…" : "Test"}
        </Button>
        {testMutation.data &&
          (testMutation.data.ok ? (
            <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
              {typeof testMutation.data.result === "string"
                ? testMutation.data.result
                : JSON.stringify(testMutation.data.result, null, 2)}
            </pre>
          ) : (
            <p className="text-xs text-destructive">{testMutation.data.error}</p>
          ))}
        {testMutation.isError && (
          <p className="text-xs text-destructive">
            {testMutation.error instanceof Error ? testMutation.error.message : "Test failed."}
          </p>
        )}
      </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- transform.test.tsx`
Expected: 5 passed.

- [ ] **Step 5: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/transform.tsx frontend/components/composer/canvas/node-panels/transform.test.tsx
git commit -m "feat: add 'Test expression' section to the transform node panel"
```

---

### Task 11: data-transform.tsx — Test expression section

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/data-transform.tsx`
- Modify: `frontend/components/composer/canvas/node-panels/data-transform.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add to the top of `frontend/components/composer/canvas/node-panels/data-transform.test.tsx`, replacing the current first line (`import { describe, it, expect, vi } from "vitest";`):

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import DataTransformPanel from "./data-transform";

const { listExecutions } = vi.hoisted(() => ({ listExecutions: vi.fn() }));
vi.mock("@/lib/api/executions", () => ({ listExecutions }));

const { evaluateDataTransformExpression } = vi.hoisted(() => ({
  evaluateDataTransformExpression: vi.fn(),
}));
vi.mock("@/lib/api/expressions", () => ({ evaluateDataTransformExpression }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  listExecutions.mockResolvedValue({ total: 0, items: [], limit: 1, offset: 0 });
});
```

(This removes the old direct `import DataTransformPanel from "./data-transform";` line since it's now included above, and drops the redundant `import { render, screen, fireEvent } from "@testing-library/react";` in favor of the version above that also imports `waitFor`.)

Every existing `render(<DataTransformPanel .../>)` call in this file must be wrapped in `wrap(...)`. There are 6 call sites — replace each exactly as follows (props unchanged, only the `render(...)` wrapping changes):

1. In `it("renders operation choices and calls onChange with operation", ...)`:
   Replace `render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);`
   with `render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));`

2. In `it("editing the collection field calls onChange with collection", ...)`:
   Replace `render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);`
   with `render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));`

3. In `it("editing the expression field calls onChange with expression", ...)`:
   Replace `render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);`
   with `render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));`

4. In `it("editing the item variable name calls onChange with itemVar", ...)`:
   Replace `render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);`
   with `render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));`

5. In `it("shows the initial-value field only when operation is reduce, and calls onChange with initial", ...)`:
   Replace
   ```tsx
   render(
     <DataTransformPanel data={{ operation: "reduce" }} onChange={onChange} currentNodeId="dt-1" />
   );
   ```
   with
   ```tsx
   render(
     wrap(<DataTransformPanel data={{ operation: "reduce" }} onChange={onChange} currentNodeId="dt-1" />)
   );
   ```

6. In `it("describes the expression language as simpleeval, not JSONPath/Handlebars", ...)`:
   Replace `render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);`
   with `render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));`

Add these new tests at the end of the `describe("DataTransformPanel", ...)` block, before its closing `});`:

```tsx
  it("Test button is disabled when the collection or expression is empty", () => {
    render(wrap(<DataTransformPanel data={{}} onChange={vi.fn()} currentNodeId="dt-1" />));
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();
  });

  it("runs a map test and shows the result array", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: true,
      result: [2, 4, 6],
      error: null,
      itemCount: 3,
      truncated: false,
    });
    render(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['nums']", expression: "item * 2" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/\[.*2.*4.*6.*\]|2,\s*4,\s*6/s)).toBeInTheDocument());
    expect(evaluateDataTransformExpression).toHaveBeenCalledWith({
      operation: "map",
      collection: "variables['nums']",
      expression: "item * 2",
      itemVar: "item",
      initial: undefined,
      variables: {},
    });
  });

  it("shows the truncation banner when the backend reports truncated", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: true,
      result: [1],
      error: null,
      itemCount: 50,
      truncated: true,
    });
    render(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['nums']", expression: "item" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/first 50/i)).toBeInTheDocument());
  });

  it("shows the error message on a failed test", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: false,
      result: null,
      error: "collection: simpleeval failed evaluating \"variables['missing']\": KeyError",
      itemCount: null,
      truncated: false,
    });
    render(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['missing']", expression: "item" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/KeyError/)).toBeInTheDocument());
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- data-transform.test.tsx`
Expected: FAIL — no "Test" button exists yet in `data-transform.tsx`.

- [ ] **Step 3: Add the Test expression section**

In `frontend/components/composer/canvas/node-panels/data-transform.tsx`, replace the imports:

```tsx
"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
```
with:
```tsx
"use client";

import { useMutation } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { evaluateDataTransformExpression } from "@/lib/api/expressions";
import { SampleStateField, useSampleState } from "./sample-state-field";
```

Replace the component signature and body opening:
```tsx
export default function DataTransformPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  const operation = (data.operation as string) ?? "map";

  return (
```
with:
```tsx
export default function DataTransformPanel({
  data,
  onChange,
  workflowId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
  workflowId?: string;
}) {
  const operation = (data.operation as string) ?? "map";
  const collection = (data.collection as string) ?? "";
  const expression = (data.expression as string) ?? "";
  const itemVar = (data.itemVar as string) ?? "item";

  const sampleState = useSampleState(workflowId);
  const testMutation = useMutation({
    mutationFn: () =>
      evaluateDataTransformExpression({
        operation,
        collection,
        expression,
        itemVar,
        initial: data.initial,
        variables: sampleState.parsed ?? {},
      }),
  });

  return (
```

Add the new section right before the closing `</div>` at the end of the component's returned JSX (after the `{operation === "reduce" && (...)}` block, still inside the outer `<div className="space-y-4">`):

```tsx
      <div className="space-y-2 border-t pt-4">
        <Label>Test expression</Label>
        <SampleStateField
          text={sampleState.text}
          onChangeText={sampleState.setText}
          error={sampleState.error}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={
            !collection || !expression || sampleState.error !== null || testMutation.isPending
          }
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending ? "Testing…" : "Test"}
        </Button>
        {testMutation.data &&
          (testMutation.data.ok ? (
            <div className="space-y-1">
              {testMutation.data.truncated && (
                <p className="text-xs text-amber-600">
                  Tested against the first {testMutation.data.itemCount} items — the
                  full collection has more.
                </p>
              )}
              <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
                {JSON.stringify(testMutation.data.result, null, 2)}
              </pre>
            </div>
          ) : (
            <p className="text-xs text-destructive">{testMutation.data.error}</p>
          ))}
        {testMutation.isError && (
          <p className="text-xs text-destructive">
            {testMutation.error instanceof Error ? testMutation.error.message : "Test failed."}
          </p>
        )}
      </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- data-transform.test.tsx`
Expected: all tests pass (existing ones + 4 new ones).

- [ ] **Step 5: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/data-transform.tsx frontend/components/composer/canvas/node-panels/data-transform.test.tsx
git commit -m "feat: add 'Test expression' section to the data-transform node panel

Runs the real map/filter/reduce loop over the sample collection (via
the backend's evaluate-data-transform endpoint), not just a single
item, so reduce and filter previews show a real end-to-end result."
```

---

### Task 12: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `.venv/Scripts/python.exe -m ruff check src tests && .venv/Scripts/python.exe -m ruff format --check src tests && .venv/Scripts/python.exe -m pyright src tests && .venv/Scripts/python.exe -m pytest -m "not integration" --no-cov -q`
Expected: ruff clean, pyright 0 errors, all tests pass (pre-existing 1230 + 4 data-transform-helper tests + 10 expressions-endpoint tests = 1244).

- [ ] **Step 2: Full frontend suite**

Run (from `frontend/`): `npm test && npx tsc --noEmit`
Expected: all vitest tests pass, tsc clean.

- [ ] **Step 3: Manual smoke test**

1. Start the backend: `.venv/Scripts/python.exe -m uvicorn src.main:app --reload`
2. Start the frontend: `cd frontend && npm run dev`
3. Open the Designer for any workflow containing (or add) a `transform` node.
4. In its panel, enter an expression like `1 + 1` in the "Expression" field.
5. Scroll to "Test expression", confirm the sample-state textarea shows `{}` (or real data if the workflow has a prior execution).
6. Click "Test" — confirm the result `2` renders.
7. Change the expression to something invalid, e.g. `nonexistent_var + 1`, click "Test" again — confirm a red error message appears instead of a crash.
8. Repeat steps 3-7 for a `data-transform` node with `operation=map`, `collection=variables['nums']`, `expression=item * 2`, and a sample-state textarea containing `{"nums": [1, 2, 3]}` — confirm the result `[2, 4, 6]` renders.

- [ ] **Step 4: Report completion**

No commit for this task — it's verification only. If any step fails, return to the relevant task and fix before considering the plan complete.
