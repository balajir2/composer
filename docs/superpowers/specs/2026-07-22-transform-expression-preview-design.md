# Design: "Test this expression" preview for transform / data-transform panels

**Date:** 2026-07-22
**Status:** Approved, ready for implementation plan

## Problem

`transform` and `data-transform` node panels let designers write raw `simpleeval`
expressions (see `src/executors/_eval.py`) with no way to try them out before
running a full workflow execution. A typo, a wrong field name, or a
misunderstanding of what's in scope only surfaces after a real execution fails —
slow feedback for what's usually a one-line bug.

## Goal

Add an in-panel "Test expression" section to both `transform.tsx` and
`data-transform.tsx` that evaluates the designer's current (possibly unsaved)
expression against sample state, entirely server-side, using the exact same
`evaluate()` primitive real executions use (ADR-0012: `evaluate()` is the only
eval primitive in Composer — no reimplementation, no client-side eval).

## Scope

Both `transform` (single expression) and `data-transform` (map/filter/reduce:
collection expression + per-item expression) get the feature. Testing never
persists anything — no workflow save, no `WorkflowExecution` row, purely a
request/response round trip against caller-supplied JSON.

## Architecture

Two new stateless endpoints in a new `src/api/expressions.py` router, both
wrapping `evaluate()` from `src/executors/_eval.py`:

- **`POST /expressions/evaluate-transform`**
  Body: `{"expression": str, "variables": dict}`
  Builds a minimal fake state (`{"variables": variables, "node_results": {}}`),
  calls `evaluate(expression, state)`, catches `EvalError`.
  Response: `{"ok": true, "result": <json>}` or `{"ok": false, "error": str}`.
  **Always HTTP 200** — a broken draft expression is the expected common case
  here, not a protocol error.

- **`POST /expressions/evaluate-data-transform`**
  Body: `{"operation": "map"|"filter"|"reduce", "collection": str, "expression": str, "itemVar": str, "initial": <json, optional>, "variables": dict}`
  Evaluates `collection` against the fake state first (same ok/error envelope
  on failure). If the result isn't a `list`/`tuple`, returns
  `{"ok": false, "error": "collection evaluated to <type>, expected a list"}`.
  Otherwise truncates to the **first 50 items** (a defensive cap — this
  endpoint accepts a client-supplied collection directly, unlike real
  execution which is bounded by workflow input size limits) and runs the real
  map/filter/reduce loop via a function extracted from
  `DataTransformExecutor` (see below).
  Response: `{"ok": true, "result": <json>, "itemCount": int, "truncated": bool}`
  or `{"ok": false, "error": str}`.

Both endpoints require only `Depends(get_current_role)` (any authenticated
user) — no workflow ownership check, since no workflow is read or written.

## Backend refactor: shared map/filter/reduce loop

`src/executors/data_transform.py`'s `DataTransformExecutor.arun` currently
inlines the map/filter/reduce loop. Extract it into a standalone function:

```python
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
    evaluate-data-transform test endpoint (preview), so the preview can
    never drift from real execution behavior.
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

`DataTransformExecutor.arun` calls this after evaluating `collection` (same as
today, just moved into the shared function). `DataTransformNodeError` wrapping
of `EvalError` stays in `arun`; the test endpoint does its own `EvalError`
catch → `{"ok": false, ...}` envelope, since it has no node id to attach to an
error message.

## Frontend — sample state sourcing (shared by both panels)

A shared piece (hook or small component) used by both panels:

- On mount, calls the existing `GET /executions?workflowId={id}&limit=1`
  (already sorted `startedAt desc` — no new endpoint needed) to fetch the most
  recent execution for the current workflow.
- If an execution exists: pre-fills a JSON textarea with its `variables`,
  pretty-printed.
- If none exists: textarea starts as `{}`.
- Textarea is always user-editable — pre-fill is a convenience, not a lock.
- Invalid JSON in the textarea disables the Test button with an inline
  "Invalid JSON" message (same validation-message pattern as
  `describeOutputKeyError` in `transform.tsx`); nothing is sent to the backend
  until it parses.

`workflowId` is already available to node panels via the canvas context (the
same context `designer-execution-panel.tsx` uses).

## Frontend — `transform.tsx`

Below the existing Expression textarea: a "Test expression" section with:
- The shared sample-state textarea.
- A `Test` button: `useMutation` → `POST /expressions/evaluate-transform`,
  disabled while pending (same pattern as `McpTestButton`).
- Result area:
  - Success: `<pre>` block with `JSON.stringify(result, null, 2)` (or the raw
    string if `result` is already a string) — same rendering convention as
    `execution.output` in `designer-execution-panel.tsx`.
  - Error: destructive-colored text with the `EvalError` message verbatim.

## Frontend — `data-transform.tsx`

Same "Test expression" section. Result area varies by `operation`:
- **map**: the full output array (or a truncated preview if long).
- **filter**: `"N of M items kept"` plus the kept items.
- **reduce**: the single final value.
- Whenever `truncated` is `true`: a banner, `"Tested against the first 50 of
  {itemCount + remaining} items."` (exact wording finalized during
  implementation — the important part is surfacing that truncation happened).

## Error handling & edge cases

- No prior execution → empty `{}` sample, no error.
- Invalid JSON in sample-state textarea → Test button disabled, inline error,
  nothing sent to backend.
- Expression references an undefined name (`NameNotDefined`) → surfaces as the
  normal `EvalError` message; this is often exactly the bug being hunted.
- `itemVar` colliding with a reserved scope name (`variables`, `lastOutput`,
  `node_results`) → already rejected inside `_eval.py`'s scope-building, which
  both real execution and the test endpoint route through — no separate
  handling needed.
- Network/backend error → `useMutation`'s `onError`, rendered inline in the
  same result area (not a toast, to stay consistent with in-panel results).

## Testing plan

- **Backend unit** (`tests/unit/api/test_expressions.py`, new): both
  endpoints — success/error envelopes, the 50-item truncation and
  `truncated`/`itemCount` fields, non-list collection error, reserved-name
  rejection.
- **Backend unit** (extend `tests/unit/executors/test_data_transform.py`):
  confirm `DataTransformExecutor` and `run_map_filter_reduce` produce
  identical output for the same inputs — the guarantee the refactor exists
  for.
- **Frontend** (`vitest`, extend `transform.test.tsx` / add
  `data-transform.test.tsx` coverage if not already present): mocked API
  calls, asserting result/error rendering and the truncation banner.
- No integration/E2E test needed — no external service, no DB writes beyond
  the pre-existing `GET /executions` read.
