# Phase 4b — Control-flow executors (if-else, while)

**Target exit date:** 2026-04-22
**Author:** Composer team
**Depends on:** Phase 4a complete (the `_eval.py` simpleeval wrapper is the routing engine).
**Split parent:** Phase 4 decomposed into 4a (linear) + 4b (control-flow).

---

## 1. Goal

Ship two node-type executors and teach `graph_builder.py` to emit LangGraph `add_conditional_edges` for them:

| Node type | Responsibility |
|---|---|
| `if-else` | Evaluate a simpleeval boolean expression; route to the `"true"`-branch or `"false"`-branch outgoing edge. |
| `while` | Evaluate a simpleeval boolean expression each iteration; route to the `"body"`-branch while true, `"exit"`-branch on false, or error on max-iterations. |

Combined with Phase 4a, Composer can now express any OAB workflow topology except user-approval (Phase 5).

## 2. Non-goals for this phase

- `switch`/`case` multi-way branching. Users chain `if-else` nodes.
- `break` / `continue` inside a while body. Users chain an `if-else` inside the body that routes to the while's exit target.
- Parallel branches / fork-join. Phase 9 (hardening).
- Nested-loop max-iteration budget sharing. Each `while` node tracks its own counter independently.
- Async iteration. LangGraph supports it natively; Phase 4b doesn't add any Composer-specific tooling on top.
- Visual loop indicators in node_results. The audit row includes iteration count; the UI (Phase 10) decides how to display it.

## 3. Success criteria

Phase 4b exits when:

1. All unit tests pass: `ruff`, `ruff format --check`, `pyright --strict`, `pytest -m "not integration"` green on `main`.
2. Two integration tests run end-to-end against real Neon:
    - `tests/integration/test_if_else_routing.py` — a workflow that branches based on a `Start → Set-State → If-Else → [A or B] → End` shape.
    - `tests/integration/test_while_countdown.py` — a workflow that decrements a counter in a loop until it reaches zero.
3. One OAB regression port covers the if-else or while behavioural contract.
4. `grep -rn "NotImplementedError" src/` returns no hits for `"Phase 4"` — both conditional source types have implementations.
5. CHANGELOG, CLAUDE.md phase table, and `docs/design/decisions.md` all reflect 4b ✅ Complete + Phase 4 ✅ Complete.

## 4. Architecture in one diagram

```
workflow JSON                          graph_builder emits
-------------                           -----------------------
{"type":"if-else", "data":{...}}       builder.add_node("ifX", ifX_executor.arun)
  with outgoing edges:
    ifX → A (branch: "true")           builder.add_conditional_edges(
    ifX → B (branch: "false")             "ifX",
                                          _if_else_router(condition_expr),
                                          {"true": "A", "false": "B"},
                                       )

{"type":"while", "data":{...}}         builder.add_node("wY", wY_executor.arun)
  with outgoing edges:
    wY → D (branch: "body")            builder.add_conditional_edges(
    wY → E (branch: "exit")               "wY",
                                          _while_router(condition_expr, wY.id),
                                          {"body": "D", "exit": "E"},
                                       )
  with one incoming edge:
    D → wY (normal)                    builder.add_edge("D", "wY")
```

The **executor** produces a state delta (mostly pass-through — it doesn't decide the next node).
The **router** is a separate plain function closed over the condition expression + node id; LangGraph calls it on every traversal through the node.

## 5. Module layout

```
src/
  engine/
    workflow.py               CHANGED — WorkflowEdge.branch: str | None = None
                                       (validated in graph_builder at compile time)
    graph_builder.py          CHANGED — emit add_conditional_edges for {if-else, while};
                                       drop the Phase-4 NotImplementedError.
                                       New helpers: _route_if_else, _route_while,
                                       _iter_count_of(state, node_id).
  executors/
    if_else.py                NEW — IfElseExecutor produces an empty-ish state delta
                                      (records the condition's evaluated value for audit).
    while_loop.py             NEW — WhileExecutor bumps _while_iterations[node_id],
                                      raises WhileMaxIterationsError when cap hit.

tests/
  unit/
    engine/
      test_workflow_models.py        EXTENDED — WorkflowEdge accepts `branch` field.
      test_graph_builder.py          EXTENDED — conditional edges compile;
                                                 branch validation rules; max-iter
                                                 integration; mixing conditional +
                                                 normal edges on the same source
                                                 errors.
    executors/
      test_if_else_executor.py       NEW
      test_while_executor.py         NEW
  integration/
    test_if_else_routing.py          NEW — real Neon; branching on a state var.
    test_while_countdown.py          NEW — real Neon; counts down from 3 to 0.
  regression/
    test_oab_if_else_routing.py      NEW — port of OAB's behaviour.
```

## 6. `WorkflowEdge.branch`

```python
# src/engine/workflow.py  (extend)
class WorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    # Phase 4b: identifies which conditional branch this edge represents.
    # Required when source is an if-else or while node; forbidden otherwise.
    branch: str | None = None
```

**Validation** (in `graph_builder.validate_workflow_shape`):

- Edges whose `source` is an `if-else` node must have `branch in {"true", "false"}`.
- Edges whose `source` is a `while` node must have `branch in {"body", "exit"}`.
- Edges whose source is any other node MUST have `branch=None`.
- Each if-else node must have exactly one `"true"` edge and one `"false"` edge.
- Each while node must have exactly one `"body"` edge and one `"exit"` edge.

Missing / wrong branch values raise `WorkflowValidationError` at build time — before any execution starts, so failures are fast and user-facing.

## 7. `IfElseExecutor`

### 7.1 Node data shape

Phase 1 already declared:

```python
class IfElseNodeData(BaseNodeData):
    condition: str | None = None
    true_path: str | None = Field(default=None, alias="truePath")
    false_path: str | None = Field(default=None, alias="falsePath")
    true_label: str | None = Field(default=None, alias="trueLabel")
    false_label: str | None = Field(default=None, alias="falseLabel")
```

We use only `condition` — `true_path`/`false_path` are legacy-for-UI fields that duplicate edge information. The authoritative branching target is the edge's `target`, not the data-level field.

### 7.2 Behaviour

```python
# src/executors/if_else.py  (new)

@register_executor("if-else")
class IfElseExecutor:
    def __init__(self, node: IfElseNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        # Evaluate condition; record the boolean in node_results for audit.
        # Routing itself is done by the router closure in graph_builder.
        expr = self.node.data.condition
        if not expr:
            raise ValueError(f"if-else node {self.node.id!r} requires condition")
        try:
            result = evaluate(expr, state)
        except EvalError as exc:
            raise IfElseNodeError(f"if-else node {self.node.id!r}: {exc}") from exc

        taken = "true" if result else "false"
        return {
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"condition": expr},
                    "output": {"taken": taken, "evaluated": result},
                }
            },
        }
```

### 7.3 Router

```python
# src/engine/graph_builder.py  (new helper)

def _route_if_else(node: IfElseNode) -> Callable[[WorkflowStateDict], str]:
    """Return a router that re-evaluates condition on each traversal.

    LangGraph calls this after the node's arun() returns.  We don't trust
    node_results (it might be stale on a retry) — evaluate fresh.
    """
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "false"
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "false"
        return "true" if result else "false"

    return _router
```

The executor AND the router both evaluate the condition; slightly redundant but keeps the audit row accurate and the router side-effect-free. OAB does the same.

## 8. `WhileExecutor`

### 8.1 Node data shape

Phase 1 declared:

```python
class WhileNodeData(BaseNodeData):
    condition: str | None = None
    max_iterations: int = Field(default=100, alias="maxIterations")
```

(If the existing field is `max_iterations` with a different default, keep it — document in the spec.)

Add nothing new; 100 is Composer's chosen default. Per-node override via JSON.

### 8.2 Behaviour

```python
# src/executors/while_loop.py  (new)

_MAX_ITER_STATE_KEY = "_while_iterations"

@register_executor("while")
class WhileExecutor:
    def __init__(self, node: WhileNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        expr = self.node.data.condition
        if not expr:
            raise ValueError(f"while node {self.node.id!r} requires condition")

        # Bump iteration counter; fail-fast if cap is hit.
        counts = dict((state.get("variables") or {}).get(_MAX_ITER_STATE_KEY) or {})
        counts[self.node.id] = counts.get(self.node.id, 0) + 1
        if counts[self.node.id] > self.node.data.max_iterations:
            raise WhileMaxIterationsError(
                f"while node {self.node.id!r} exceeded max_iterations="
                f"{self.node.data.max_iterations}"
            )

        try:
            result = evaluate(expr, state)
        except EvalError as exc:
            raise WhileNodeError(f"while node {self.node.id!r}: {exc}") from exc

        taken = "body" if result else "exit"
        return {
            "variables": {_MAX_ITER_STATE_KEY: counts},
            "current_node_id": self.node.id,
            "node_results": {
                self.node.id: {
                    "node_id": self.node.id,
                    "status": "completed",
                    "input": {"condition": expr, "iteration": counts[self.node.id]},
                    "output": {"taken": taken, "evaluated": result},
                }
            },
        }
```

### 8.3 Router

```python
def _route_while(node: WhileNode) -> Callable[[WorkflowStateDict], str]:
    expr = node.data.condition or ""

    def _router(state: WorkflowStateDict) -> str:
        if not expr:
            return "exit"
        # Cap check happens in the executor before the router runs.
        try:
            result = evaluate(expr, state)
        except EvalError:
            return "exit"
        return "body" if result else "exit"

    return _router
```

### 8.4 Max-iteration design

- Counter lives in `state["variables"]["_while_iterations"]` (a dict keyed by node id).
- Bumped by the executor. If `count > max_iterations`, executor raises `WhileMaxIterationsError`.
- LangGraphExecutor's existing exception-catching marks the execution `failed` with the error message. No magic.
- Underscore prefix on the key tells workflow authors not to read it from their own expressions — but we don't actively block access (simpleeval accepts it).

## 9. `graph_builder.py` changes

### 9.1 Remove the Phase-4 NotImplementedError block

Current code:

```python
for edge in workflow.edges:
    source_node = nodes_by_id[edge.source]
    if source_node.type in CONDITIONAL_SOURCE_TYPES:
        phase = 4 if source_node.type in {"if-else", "while"} else 5
        raise NotImplementedError(
            f"Conditional edges from node type {source_node.type!r} land in Phase {phase}."
        )
```

Replace with:

```python
CONDITIONAL_SOURCE_TYPES = {"if-else", "while", "user-approval"}  # unchanged

# In the edge loop:
for edge in workflow.edges:
    source_node = nodes_by_id[edge.source]
    if source_node.type in {"if-else", "while"}:
        continue  # handled below via add_conditional_edges
    if source_node.type == "user-approval":
        raise NotImplementedError(
            f"Conditional edges from node type 'user-approval' land in Phase 5."
        )
    # ... existing normal-edge logic ...
    if source_node.type == "note":
        continue
    if nodes_by_id[edge.target].type == "note":
        continue
    builder.add_edge(edge.source, edge.target)

# Emit conditional edges after all normal edges and nodes are in place
for node in workflow.nodes:
    if node.type == "if-else":
        mapping = _branch_mapping(node, workflow.edges, {"true", "false"})
        builder.add_conditional_edges(node.id, _route_if_else(node), mapping)
    elif node.type == "while":
        mapping = _branch_mapping(node, workflow.edges, {"body", "exit"})
        builder.add_conditional_edges(node.id, _route_while(node), mapping)
```

### 9.2 `_branch_mapping` helper

```python
def _branch_mapping(
    node: WorkflowNode,
    edges: list[WorkflowEdge],
    required_branches: set[str],
) -> dict[str, str]:
    """Build a {branch: target_node_id} mapping for a conditional source.

    Validates that the edges-out set exactly matches required_branches — no
    missing, no extras, no duplicates.  Raises WorkflowValidationError.
    """
    out_edges = [e for e in edges if e.source == node.id]
    seen: dict[str, str] = {}
    for e in out_edges:
        if e.branch is None:
            raise WorkflowValidationError(
                f"Edge {e.id!r} leaving {node.type} node {node.id!r} has no branch label."
            )
        if e.branch in seen:
            raise WorkflowValidationError(
                f"Duplicate branch {e.branch!r} on edges leaving {node.id!r}."
            )
        seen[e.branch] = e.target
    missing = required_branches - seen.keys()
    extra = seen.keys() - required_branches
    if missing or extra:
        raise WorkflowValidationError(
            f"{node.type} node {node.id!r} branches mismatch: "
            f"missing={sorted(missing)}, unexpected={sorted(extra)}."
        )
    return seen
```

### 9.3 Validation rules elsewhere

`validate_workflow_shape` gains a check: for every edge, if the source is NOT a conditional type, the edge's `branch` must be `None`. Prevents authors from putting branch labels on normal edges and having them silently ignored.

## 10. Test plan

### 10.1 Unit tests

- `if-else` executor: 5 tests (truthy, falsy, missing condition → ValueError, EvalError wrap → IfElseNodeError, registry)
- `while` executor: 7 tests (loop iteration counter increments, hit-cap → WhileMaxIterationsError, exit on false, body on true, missing condition → ValueError, EvalError wrap, registry)
- Graph builder conditional edges: 6 tests (happy-path compile, missing branch on conditional source → validation error, branch on normal-source → validation error, missing branch in mapping → validation error, extra branch → validation error, while loops back via a normal edge)
- Router functions: 3 tests (`_route_if_else` evaluates fresh, `_route_while` delegates to simpleeval, both return the fallback on EvalError)

Totals: ~21 new unit tests. Target: ~317 tests non-integration after 4b (296 + 21).

### 10.2 Integration

`tests/integration/test_if_else_routing.py` — `Start → Set-State(x=1) → If-Else(x > 0) → Set-State(result="A") | Set-State(result="B") → End`. Verify execution completes and `variables.result == "A"`. No LLM needed.

`tests/integration/test_while_countdown.py` — `Start → Set-State(counter=3) → While(counter > 0) body: Set-State(counter={{counter - 1}}) → End`. Wait — can `Set-State` evaluate an expression? It uses `substitute_in_value` which returns the rendered string. To make this work with integer math, the body needs a `transform` node: `Start → Set-State(counter=3) → While(counter > 0) body: [Transform(counter - 1) → Set-State(counter={{lastOutput}})] → End`.

Actually simpler: use a `transform` node in the body that returns the decremented value, and the while's condition reads from `lastOutput`. Test design is a small puzzle; worked out in the plan.

Both integration tests run without ANTHROPIC_API_KEY (pure state logic).

### 10.3 Regression

`tests/regression/test_oab_if_else_routing.py` — the OAB behavioural contract: condition true → true branch, false → false branch, missing condition → failure.

## 11. Phase-exit checklist

- [ ] All unit tests green: `ruff`, `pyright --strict`, `pytest -m "not integration"`.
- [ ] Both integration tests pass against real Neon.
- [ ] OAB regression port passes.
- [ ] `grep -rn "Phase 4" src/` shows no `NotImplementedError` — all Phase 4 types shipped.
- [ ] CHANGELOG Phase 4b section + **Phase 4 overall complete** note.
- [ ] CLAUDE.md phase table: 4a ✅, 4b ✅, 5 ⏭ Next.
- [ ] ADR-0013 `Implemented by` backfilled.

## 12. Risks and mitigations

**Risk 1.** LangGraph's `add_conditional_edges` semantics change between versions. **Mitigation:** 0.4.x is pinned in `pyproject.toml`; upgrades go through a targeted test run.

**Risk 2.** Infinite loop authors construct by setting a while's condition to `True` and not mutating state. **Mitigation:** `max_iterations=100` default with per-node override; the counter lives in state so checkpoint/restore works. Hit → `WhileMaxIterationsError`, execution marked failed with node id.

**Risk 3.** Users draw a while node with three outgoing edges (body, exit, some third branch), expecting multi-way semantics. **Mitigation:** `_branch_mapping`'s "unexpected branch" check rejects it at build time with a clear error naming the offending edge.

**Risk 4.** `_route_*` evaluates the condition a second time after the executor just did — wasted work. **Mitigation:** simpleeval evaluation is microseconds; not a real hot path. If it shows up in profiling we can cache via a `(node_id, state_hash)` key.

**Risk 5.** Loop-back normal edges (body → while) confuse the reachability BFS in `validate_workflow_shape`. **Mitigation:** BFS visits each node once; cycles terminate naturally. Existing reachability validator already handles this.

## 13. New ADR

### ADR-0013: Conditional edges carry a `branch` label on `WorkflowEdge`

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Phase 4b introduces conditional routing (`if-else`, `while`). Two options for how graph_builder knows which outgoing edge represents which branch:

1. **Runtime inference from node data.** Read `if_else_node.data.true_path` / `false_path` — the legacy-UI fields — as target-node ids. Pro: no edge schema change. Con: two sources of truth (edge + node-data); user draws an edge to a different target and the data field silently wins.
2. **Position-based.** "The first outgoing edge is the true branch, the second is false." Pro: no schema change, no user config. Con: edge-list order is not stable across serialise/deserialise; brittle.
3. **Explicit label on the edge.** Add `branch: str | None` to `WorkflowEdge`. Validation: conditional source requires branch; normal source forbids it. Pro: single source of truth (the edge), validation is local, UI layer can author edges freely. Con: minor schema change.

**Decision.** Option 3. `WorkflowEdge` gains `branch: str | None = None`. Accepted values vary by source type: `{"true","false"}` for `if-else`, `{"body","exit"}` for `while`. `graph_builder` builds its routing mapping from edge labels at compile time; the legacy-UI fields `true_path` / `false_path` become UI-only and the executor / router ignore them.

**Alternatives considered.** Above.

**Consequences.**
- Workflow JSON schema grows one optional field. Pydantic v2's optional-field handling makes this back-compatible: pre-4b workflows (with no `branch`) still validate as Phase 1–3 shape.
- Phase 10 (UI) can auto-assign `branch="true"` / `"false"` when the user drags edges out of an if-else node. No UI logic for Composer to ship.
- Authors of programmatic workflow generators (tests, scripts) must set `branch` on conditional-source edges.

**Implemented by.** Phase 4b (commits TBD).

**Related.** ADR-0002 (workflow schema), ADR-0012 (simpleeval is the only eval primitive — the router uses it), [Phase 4b spec](../superpowers/specs/2026-04-21-phase-4b-control-flow-design.md).

## 14. Execution handoff

After this spec is approved and committed, the plan is written to `docs/superpowers/plans/2026-04-21-phase-4b-control-flow-plan.md` and executed via `superpowers:subagent-driven-development`, same cadence as Phases 1–4a. ~9 tasks; one commit per task.
