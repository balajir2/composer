# Design: Deterministic final-output for fan-out / multiple End nodes

**Date:** 2026-07-20
**Status:** Approved (design phase) — implementation plan not yet written
**Origin:** A narrowly-scoped carve-out of `docs/claude-improvement-backlog.md`'s P0-8 ("Strengthen
graph-shape and terminal-path validation"), which bundles several separable concerns. This spec
covers only the fan-out/fan-in determinism piece — investigated earlier this session while
designing a combined-workflow architecture for an unrelated feature, and confirmed as a genuine,
reproducible data-loss bug rather than a hypothetical one. The rest of P0-8 (reachability computed
from the wrong edge set, Start/End edge direction misuse, cycle detection for non-`While` loops, a
unified validation/compilation graph representation) stays a separate, future backlog item — no
Macy's-specific or other customer-specific literals appear anywhere in this design; this is generic
platform capability.

## Why this spec exists

Composer's LangGraph-based execution engine already supports genuine parallel fan-out (a node with
2+ plain, unconditional outgoing edges) and fan-in (multiple nodes converging on one downstream
node) via LangGraph's native superstep parallelism and Composer's `merge_dict`/`last_wins` state
reducers (`src/engine/state.py`). This was verified working correctly for the common case: nodes
writing to *distinct* `variables` keys accumulate cleanly regardless of concurrent execution order,
and `node_results` (keyed per-node-id) already demonstrates the correct pattern for concurrent,
disjoint-key writes.

However, `src/executors/end.py`'s `EndExecutor` writes to a single, shared,
non-node-id-keyed key: `variables["finalOutput"]`. If a workflow's topology allows two `End` nodes
to fire in the *same* LangGraph superstep (e.g. two branches of a plain fan-out, each independently
terminating at its own `End` node, never reconverging first), both nodes' deltas target the exact
same key. `merge_dict`'s shallow `{**left, **right}` merge means whichever delta happens to be
applied last — an order LangGraph does not guarantee for genuinely parallel nodes — silently wins.
The other branch's output is lost, non-deterministically, with no error surfaced anywhere. This is
confirmed, not hypothetical: `graph_builder.py` already compiles every `End`-typed node with its own
`add_edge(end_id, END)` (verified at `src/engine/graph_builder.py:365`), so a workflow with two
independent `End` nodes fed by parallel branches compiles and *appears* to run successfully today —
it just silently produces a coin-flip result.

## Decisions locked by user Q&A during brainstorming

- **Scope**: only the final-output/multi-End determinism piece of P0-8. Not reachability
  computation, not Start/End edge-direction validation, not cycle detection — those stay a separate
  future backlog item, tracked as-is in `docs/claude-improvement-backlog.md`.
- **Approach: redefine `finalOutput` to be collision-safe by design, not reject the topology.**
  The alternative considered (reject workflows with 2+ reachable `End` nodes at validation time) was
  explicitly rejected by the user — it would be strictly *more* restrictive than needed, since an
  `if-else`/`while` branch structure where each branch terminates at a *different* `End` node is
  already perfectly safe today (conditional branches are mutually exclusive — only one path ever
  executes, so only one `End` node ever fires; no race exists in that case, and no change is needed
  for it). Redefining the final-output storage mechanism instead preserves that legitimate,
  currently-safe pattern *and* fixes the genuinely unsafe parallel-fan-out case, without needing to
  distinguish the two cases in validation logic at all.
- **`current_node_id`'s `last_wins` reducer stays unchanged.** Once all branches of a run reach their
  respective `End` nodes, which node is considered "current" is a cosmetic detail (used for
  progress/SSE-WebSocket display) that doesn't affect the correctness of the persisted output — a
  fundamentally different severity than the data-loss bug this spec fixes. Not addressed here.

## The fix

### Component 1 — new state field (`src/engine/state.py`)

Add a new top-level field to `WorkflowStateDict`, using the *same* `merge_dict` reducer already
proven correct for concurrent, disjoint-key writes (the same pattern `node_results` already uses):

```python
class WorkflowStateDict(TypedDict):
    variables: Annotated[dict[str, Any], merge_dict]
    chat_history: Annotated[list[ChatMessage], add]
    current_node_id: Annotated[str, last_wins]
    node_results: Annotated[dict[str, NodeExecutionResult], merge_dict]
    pending_auth: Annotated[dict[str, Any] | None, last_wins]
    loop_results: Annotated[list[Any], add]
    final_outputs: Annotated[dict[str, Any], merge_dict]  # NEW
    user_id: NotRequired[str]
```

`initial_state()` initializes it to `{}`, mirroring `node_results`'s empty-dict default.

**Why this is collision-safe by construction:** each `End` node will key its contribution by its
*own node id* (Component 2 below) — never a shared literal key. Two `End` nodes writing
`{"final_outputs": {"end-a": valA}}` and `{"final_outputs": {"end-b": valB}}` concurrently produce
disjoint-key deltas; `merge_dict`'s shallow union accumulates both correctly *regardless of which
delta LangGraph happens to apply first* — the same proof already borne out by `node_results`'s
existing, working behavior under concurrent writes.

### Component 2 — `EndExecutor` (`src/executors/end.py`)

Replace the current single-key write:

```python
async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
    last_output = state["variables"].get("lastOutput")
    return {
        "variables": {"finalOutput": last_output},
        "current_node_id": self.node.id,
        "node_results": {
            self.node.id: {"node_id": self.node.id, "status": "completed", "output": last_output}
        },
    }
```

with a write to the new, node-id-keyed field instead of the shared `variables["finalOutput"]` key:

```python
async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
    last_output = state["variables"].get("lastOutput")
    return {
        "final_outputs": {self.node.id: last_output},
        "current_node_id": self.node.id,
        "node_results": {
            self.node.id: {"node_id": self.node.id, "status": "completed", "output": last_output}
        },
    }
```

`node_results` and `current_node_id` are unchanged (the former was already collision-safe per the
per-node-id keying it already uses; the latter is explicitly out of scope per the Q&A above).

### Component 3 — deriving the persisted `output` (`src/engine/langgraph_executor.py`)

`_mark_completed` currently reads the racy shared key:

```python
"output": Json(
    final_vars["finalOutput"] if "finalOutput" in final_vars else final_vars.get("lastOutput")
),
```

Replace with logic that collapses the new `final_outputs` dict to a single scalar in the common
(single-`End`) case — preserving byte-for-byte identical persisted `output` for every existing
workflow, which all have exactly one `End` node — and falls back to the full dict only when there
are genuinely multiple contributors:

```python
final_outputs: dict[str, Any] = final_state.get("final_outputs") or {}
if len(final_outputs) == 1:
    (output_value,) = final_outputs.values()
elif len(final_outputs) > 1:
    output_value = final_outputs
else:
    # No End node reached (e.g. a run that errored before completion) --
    # unchanged fallback behavior.
    output_value = final_vars.get("lastOutput")
```

then `"output": Json(output_value)` in the existing `db.workflowexecution.update(...)` call.
`variables` itself is persisted unchanged (`Json(final_vars)`) — it no longer contains a
`finalOutput` key at all, since `EndExecutor` no longer writes one; this is fine since nothing else
reads `variables["finalOutput"]` (confirmed — the only consumer was this exact code path).

**No Prisma schema migration needed** — `WorkflowExecution.output` is already `Json?`
(`prisma/schema.prisma`), so persisting a dict instead of a scalar in the multi-`End` case is a
schema-compatible value change, not a type change.

**No frontend change needed** — `designer-execution-panel.tsx`'s "Final output" rendering already
handles arbitrary JSON generically:

```tsx
{typeof execution.output === "string" ? execution.output : JSON.stringify(execution.output, null, 2)}
```

A dict value renders as pretty-printed JSON automatically; a single scalar (the common case) renders
exactly as it does today.

## Testing

- **The definitive fan-out/fan-in proof**: a new test building a synthetic workflow with a genuine
  plain (unconditional) fan-out — one node with two outgoing edges, each branch independently
  reaching its *own* `End` node without reconverging — compiling it via `build_graph` and running it
  for real via the compiled graph's `ainvoke`, then asserting `final_state["final_outputs"]` contains
  *both* End nodes' outputs with no data loss. This is the test that actually closes the P0-8 gap
  documented for fan-out/fan-in: proof that the previously-racy topology is now safe, not just an
  assertion that the code compiles.
- **Regression coverage**: every existing single-`End`-node workflow test (there are many, across
  `test_graph_builder.py`, `test_langgraph_executor.py`, and integration tests) must continue to
  produce byte-for-byte identical `output` values — the collapse-to-scalar logic in Component 3 is
  specifically designed to guarantee this, and existing tests serve as the regression oracle without
  needing modification.
- **Unit tests for `EndExecutor`**: confirm it now writes `final_outputs[node_id]`, not
  `variables["finalOutput"]`, and that `node_results`/`current_node_id` writes are unchanged.
- **Unit tests for `_mark_completed`'s new derivation logic**: single-entry dict → scalar output;
  multi-entry dict → dict output; empty dict (no `End` reached) → falls back to `lastOutput`,
  matching today's fallback behavior for that case.

## Explicitly deferred to later sub-projects

- The rest of P0-8: reachability computed from the executable-edge projection (not raw edges
  including visual-only Note/File-Trigger passthroughs), rejecting edges into `Start`/out of `End`,
  requiring every executable node to reach an `End`, detecting unbounded non-`While` cycles, and
  unifying the validation and compilation graph representations.
- Any validation that would reject or warn about a *genuinely* unsafe fan-out-to-multiple-Ends
  topology at workflow-save time — this spec makes the outcome deterministic and lossless, but does
  not add any new validation error. A designer who fans out into two `End` nodes now reliably gets
  both outputs back (as a dict) rather than losing one — whether that's the *intended* shape of their
  workflow is a modeling question for them, not something this spec judges.
