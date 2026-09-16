# Design: Deterministic final-output for fan-out / multiple End nodes, plus an explicit Join node

**Date:** 2026-07-20
**Status:** Approved (design phase) — implementation plan not yet written
**Origin:** A narrowly-scoped carve-out of `docs/claude-improvement-backlog.md`'s P0-8 ("Strengthen
graph-shape and terminal-path validation"), which bundles several separable concerns. This spec
covers the fan-out/fan-in determinism piece — investigated earlier this session while designing a
combined-workflow architecture for an unrelated feature, and confirmed as a genuine, reproducible
data-loss bug rather than a hypothetical one — plus an explicit `join` node type and an
`End`-incoming-edge-count validation rule that emerged from a follow-up discussion on the same
topic. The rest of P0-8 (reachability computed from the wrong edge set, Start/End edge direction
misuse beyond what this spec adds, cycle detection for non-`While` loops, a unified
validation/compilation graph representation) stays a separate, future backlog item — no
customer-specific literals appear anywhere in this design; this is generic
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

- **Scope**: the final-output/multi-End determinism piece of P0-8, plus a new explicit `join` node
  type and an `End`-incoming-edge-count validation rule that emerged from the same discussion (see
  below). Not the rest of P0-8: reachability computation from the wrong edge set, Start/End
  edge-direction validation, cycle detection — those stay a separate future backlog item, tracked
  as-is in `docs/claude-improvement-backlog.md`.
- **Approach: redefine `finalOutput` to be collision-safe by design, not reject the topology
  outright.** The alternative first considered (reject *any* workflow with 2+ reachable `End` nodes
  at validation time) was explicitly rejected — it would be strictly *more* restrictive than needed,
  since an `if-else`/`while` branch structure where each branch terminates at a *different* `End`
  node is already perfectly safe today (conditional branches are mutually exclusive — only one path
  ever executes, so only one `End` node ever fires; no race exists in that case). Redefining the
  final-output storage mechanism instead preserves that legitimate, currently-safe pattern *and*
  fixes the genuinely unsafe parallel-fan-out-to-independent-Ends case.
- **New: an explicit `join` node type, plus a rule that every `End` node must have exactly one
  incoming edge.** This emerged from a follow-up discussion once the `final_outputs` fix was already
  approved: rather than relying on the *implicit* pattern of "multiple branches happen to target the
  same downstream node" as the only way to fan-in, designers get an explicit, self-documenting `join`
  node (N incoming edges, exactly 1 outgoing edge — a real, executing node, not visual-only) to
  converge branches deliberately. Combined with requiring every `End` node to have exactly one
  incoming edge, this makes the graph's intent explicit: **`join`** means "combine these branches into
  one path forward" (which can then safely reach a single-incoming-edge `End`); **multiple `End`
  nodes** (still legal — via `if-else`/`while` branches, or via a plain fan-out that never
  reconverges) means "these are genuinely separate, independent final outputs," which the
  `final_outputs` fix (Components 1-3) makes safe and lossless even when several fire in the same
  superstep. Note that requiring "exactly one incoming edge per `End`" does **not**, by itself,
  eliminate the need for the `final_outputs` fix: a plain (non-conditional) fan-out node with two
  branches, each independently reaching its *own* single-incoming-edge `End` without ever
  reconverging, still satisfies "exactly one incoming edge per End" while still being the exact
  same-superstep race this spec's Components 1-3 fix.
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

### Component 4 — new `join` node type (`src/engine/workflow.py`, `src/executors/join.py`)

A genuine structural node type, mirroring `EndNodeData`'s existing "no extra fields" shape:

```python
class JoinNodeData(BaseNodeData):
    pass


class JoinNode(BaseModel):
    id: str
    type: Literal["join"]
    position: Position
    data: JoinNodeData
```

Added to the `WorkflowNode` discriminated union and `__all__`, same as every other node type.

**Not visual-only** — unlike `Note`/`File Trigger` (skipped by `graph_builder.py`, edges rewired
around them), `join` is a real node in the compiled LangGraph graph. This matters: if `join` were
visual-only and its edges got rewired to bypass it at compile time, the *compiled* graph would still
show N branches feeding directly into whatever's downstream — the actual runtime merge behavior
(and the exact same-superstep semantics this spec cares about) is determined by the compiled graph,
not the workflow JSON's edge list, so a skip-and-rewire treatment would silently defeat the entire
purpose of making convergence explicit.

`JoinExecutor` is a thin pass-through — state from concurrent branches already merges correctly by
the time LangGraph reaches this node in the same superstep (the same proven `node_results`/
`variables` merge behavior parallel branches already rely on today):

```python
"""JoinExecutor -- the `join` node type.

Explicit convergence point for parallel branches: any number of incoming
edges, exactly one outgoing edge (enforced by validate_workflow_shape).
Purely structural -- concurrent branches' state already merges correctly
via merge_dict once LangGraph reaches this node in the same superstep,
so this executor does no work beyond standard node bookkeeping.
"""

from typing import Any

from src.engine.state import WorkflowStateDict
from src.engine.workflow import JoinNode
from src.executors.base import register_executor


@register_executor("join")
class JoinExecutor:
    def __init__(self, node: JoinNode) -> None:
        self.node = node

    async def arun(self, state: WorkflowStateDict) -> dict[str, Any]:
        return {
            "current_node_id": self.node.id,
            "node_results": {self.node.id: {"node_id": self.node.id, "status": "completed"}},
        }


__all__ = ["JoinExecutor"]
```

Registered in `graph_builder.py`'s side-effect import chain like every other executor. No special
edge-emission handling needed — `join` is a plain node with plain (non-conditional) edges in and out,
already covered by the default edge-emission loop.

### Component 5 — validation: `join` needs exactly one outgoing edge, `End` needs exactly one incoming edge (`src/engine/graph_builder.py`)

In `validate_workflow_shape()`, right after the existing "must contain at least one end node" check:

```python
end_ids = [n.id for n in workflow.nodes if n.type == "end"]
if not end_ids:
    raise WorkflowValidationError("Workflow must contain at least one end node.")

for end_id in end_ids:
    incoming = [
        e
        for e in workflow.edges
        if e.target == end_id and nodes[e.source].type not in _VISUAL_ONLY_TYPES
    ]
    if len(incoming) != 1:
        raise WorkflowValidationError(
            f"End node {end_id!r} must have exactly one incoming edge from an "
            f"executable node (found {len(incoming)}). Converge multiple branches "
            "through a join node before reaching a single End."
        )

join_ids = [n.id for n in workflow.nodes if n.type == "join"]
for join_id in join_ids:
    outgoing = [
        e
        for e in workflow.edges
        if e.source == join_id and nodes[e.target].type not in _VISUAL_ONLY_TYPES
    ]
    if len(outgoing) != 1:
        raise WorkflowValidationError(
            f"Join node {join_id!r} must have exactly one outgoing edge (found {len(outgoing)})."
        )
```

Both checks filter out edges to/from `_VISUAL_ONLY_TYPES` (`{"note", "file-trigger"}`, already a
module-level constant in `graph_builder.py`) before counting — a decorative Note edge pointing at an
`End` node must not count toward its incoming-edge tally, matching how the rest of this file already
treats visual-only edges as non-executable decoration. This deliberately does *not* attempt the
broader "reachability from the correct edge projection" fix (P0-8's other, deferred concern) — it's
a narrow, local count check on each `End`/`join` node's own edges, not a graph-wide analysis.

**`if-else`/`while` branches with a different `End` per branch remain unaffected**: each branch
target already has exactly one edge from the conditional node (distinguished by branch label), so
this rule doesn't newly reject that already-safe pattern.

### Component 6 — Designer UI registration for `join`

Mirrors every other node type's registration:
- `frontend/components/composer/canvas/node-panels/join.tsx` — minimal panel, no configurable
  fields, mirroring `end.tsx`'s exact "nothing to configure" pattern:
  ```tsx
  "use client";

  export default function JoinPanel(_props: {
    data: Record<string, unknown>;
    onChange: (patch: Record<string, unknown>) => void;
    currentNodeId?: string;
  }) {
    return (
      <p className="text-sm text-muted-foreground">
        Join has no configurable fields — it merges multiple incoming branches into a single
        path forward. Connect it to exactly one downstream node.
      </p>
    );
  }
  ```
- `property-panel.tsx` (`PANEL_MAP["join"]`, `TYPE_LABELS["join"] = "Join"`)
- `tools-palette.tsx` (`{ nodeType: "join", label: "Join" }`)
- `node-visuals.ts` (an icon suggesting merge/convergence, e.g. `GitMerge` from `lucide-react`, plus
  an accent color not already in heavy use)
- `workflow-canvas.tsx` (`COMPOSER_NODE_TYPES["join"] = InnerNode`)

## Testing

- **The definitive fan-out/fan-in proof (recommended pattern)**: a new test building a synthetic
  workflow with a genuine plain (unconditional) fan-out into a `join` node, then a single edge from
  `join` to one `End` node — compiling it via `build_graph` and running it for real via the compiled
  graph's `ainvoke`, asserting the run completes with the merged state from both branches intact and
  exactly one `final_outputs` entry (since there's only one `End`). This exercises the actual
  recommended shape for combining parallel branches.
- **The multiple-independent-Ends proof**: a second synthetic workflow with a plain fan-out where
  each branch independently reaches its *own* `End` node without a `join` (still legal, since each
  `End` individually has exactly one incoming edge) — asserting `final_state["final_outputs"]`
  contains *both* End nodes' outputs with no data loss. Together with the `join` test above, this is
  what actually closes the P0-8 gap documented for fan-out/fan-in: proof that both the "combine"
  and "stay independent" shapes are now safe, not just an assertion that the code compiles.
- **Validation tests**: an `End` node with zero incoming edges is rejected; an `End` node with 2+
  incoming edges (the previously-silent-race topology) is now rejected at validation time *unless*
  routed through a `join`; a `join` node with zero or 2+ outgoing edges is rejected; a decorative
  Note edge pointing at an `End` does not count toward its incoming-edge tally.
- **Regression coverage**: every existing single-`End`-node *workflow-level* test (there are many,
  across `test_graph_builder.py`, `test_langgraph_executor.py`, and integration tests) that asserts
  on a final output *value* must continue to produce byte-for-byte identical results — the
  collapse-to-scalar logic in Component 3 is specifically designed to guarantee this, and those
  tests serve as the regression oracle unmodified. This is distinct from the unit-level tests
  below, which test `EndExecutor`/`_mark_completed` directly against the *old* mechanism and do need
  updating to the new one (two existing tests in `tests/unit/executors/test_end.py` currently assert
  `delta["variables"]["finalOutput"]` directly, and two in `tests/unit/engine/test_langgraph_executor.py`
  construct `final_state` with a `variables.finalOutput` key directly — both need rewriting to use
  `final_outputs` instead, while preserving their original intent, including the P0-7 falsy-value
  regression guard).
- **Unit tests for `EndExecutor`**: confirm it now writes `final_outputs[node_id]`, not
  `variables["finalOutput"]`, and that `node_results`/`current_node_id` writes are unchanged.
- **Unit tests for `_mark_completed`'s new derivation logic**: single-entry dict → scalar output;
  multi-entry dict → dict output; empty dict (no `End` reached) → falls back to `lastOutput`,
  matching today's fallback behavior for that case.
- **Unit tests for `JoinExecutor`**: confirm its minimal delta shape (`current_node_id`,
  `node_results` only — no `variables`/`final_outputs` writes).
- **Frontend**: a small test for `join.tsx`'s panel (renders the static message, no fields), and
  registration coverage consistent with how other node types' canvas registration was tested.

## Explicitly deferred to later sub-projects

- The rest of P0-8: reachability computed from the executable-edge projection (not raw edges
  including visual-only Note/File-Trigger passthroughs), rejecting edges into `Start`/out of `End`
  *beyond* the incoming-edge-count rule this spec adds, requiring every executable node to reach an
  `End`, detecting unbounded non-`While` cycles, and unifying the validation and compilation graph
  representations.
- Any validation on `join`'s incoming-edge count (e.g. requiring at least 2, since a 1-incoming-edge
  `join` is a pointless no-op) — not added here; a designer mid-editing a workflow may temporarily
  have an under-connected `join`, and this isn't a safety concern the way the `End`/`join`
  outgoing-edge checks are.
