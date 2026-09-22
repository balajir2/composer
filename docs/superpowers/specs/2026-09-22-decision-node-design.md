# Design: Decision node — judgment-based branching for the palette

**Date:** 2026-09-22
**Status:** Approved (design phase) — implementation plan not yet written
**Origin:** User request, arising from a spike evaluating TypeSafe AI (`jev-latest`) as a possible
backend for Composer's Guardrails executor. The spike found no case for adopting TypeSafe there
(see `project_typesafe_laya_decision_backend_eval.md` in the user's memory store), but the
underlying need it surfaced — a general "make a judgment call and branch on it" primitive usable
at any point in a flow, not just inside Guardrails' four fixed checks — was real. This spec covers
that primitive as a new palette node. Does not touch `D:/GitHub/open-agent-builder`.

## Why this spec exists

Composer has two existing decision-shaped nodes, and neither covers this case:

- **if-else** (`src/executors/if_else.py`) branches on a `simpleeval` expression over state
  variables — deterministic, free, instant, but can only express what a formula can express. It
  cannot answer "is this a refund request?" or "which department should handle this ticket?".
- **guardrails** (`src/executors/guardrails.py`) already does LLM-judgment classification, but as
  four fixed, non-branching compliance checks (pii/moderation/jailbreak/hallucination) that pass
  their verdict through as output variables rather than routing the graph.

Workflow authors need judgment-based *branching*: an LLM call that decides yes/no or picks one of
several labeled options, and routes the graph accordingly — the same shape as if-else, but backed
by a model's judgment instead of a formula.

## Decisions locked by user Q&A during brainstorming

- **New, separate node type, not an if-else extension.** if-else stays exactly as it is: a pure,
  free, deterministic node with no I/O. Blurring that contract with LLM judgment would cost more
  clarity than it saves. Decision is the judgment-based counterpart, sharing if-else's routing
  mechanism (`add_conditional_edges` via a router closure) but not its executor or data model.
- **guardrails stays separate.** Its four fixed checks aren't what Decision is for. A future
  refactor could have guardrails' checks call the same binary-judgment code Decision uses, but
  that's out of scope here — noted as a possibility, not planned.
- **Two decision shapes: binary and choice.** `binary` = yes/no, two branches (`true`/`false`),
  same branch-key shape as if-else. `choice` = pick one of N labeled options, one branch per
  option. A numeric `score` primitive (TypeSafe/Laya's third primitive) was considered and
  explicitly deferred — no concrete use case yet beyond the two branching shapes.
- **No backend abstraction.** The executor calls Composer's existing LLM stack
  (`src/llm/providers.py::build_chat_model` + `src/llm/structured_output.py::structured_invoke`)
  directly — the same pattern Extract and Agent already use. No `JudgmentProvider` interface, no
  TypeSafe/Laya wiring. If a second backend is ever justified (see the memory note above — revisit
  Laya in a few weeks), refactor into an abstraction then; building one speculatively now is exactly
  the premature-abstraction this codebase's conventions warn against.
- **Few-shot examples are optional, not required.** Zero-shot judgment is allowed — some decisions
  are unambiguous enough not to need it — but the Designer panel shows a non-blocking hint
  ("Zero-shot decisions can be inconsistent on edge cases. Consider adding 1-2 examples.") when
  `mode` is set and `examples` is empty. This doesn't gate saving the node.
- **`temperature=0.0` by default**, matching guardrails' existing default. This mitigates sampling
  randomness; it does not fix judgment inconsistency on ambiguous inputs — examples are the actual
  lever for that, which is why they exist as a first-class field rather than a settings toggle.

## Components

### Backend data model (`src/engine/workflow.py`)

New models, inserted near `IfElseNode` (~line 243) following that section's conventions:

```python
class DecisionOption(BaseModel):
    label: str
    description: str | None = None


class DecisionExample(BaseModel):
    input: str
    result: bool | None = None   # binary mode
    option: str | None = None    # choice mode


class DecisionNodeData(BaseNodeData):
    mode: Literal["binary", "choice"]
    instruction: str
    examples: list[DecisionExample] | None = None
    options: list[DecisionOption] | None = None       # choice mode only
    true_label: str | None = Field(default=None, alias="trueLabel")    # binary mode
    false_label: str | None = Field(default=None, alias="falseLabel")  # binary mode
    model: str | None = None


class DecisionNode(BaseModel):
    id: str
    type: Literal["decision"]
    position: Position
    data: DecisionNodeData
```

Validation: `mode="choice"` requires a non-empty `options` list (≥2 entries, unique labels);
`mode="binary"` ignores `options`. Enforced in `DecisionNodeData` via a `model_validator`, mirroring
how other node-data classes in this file validate cross-field invariants.

### Executor (`src/executors/decision.py`)

New file, structured like `if_else.py` (produces the audit row; the router closure in
`graph_builder.py` does the actual routing) but with an LLM call like `guardrails.py`'s `_run_check`:

- Resolves input text the same way guardrails does: `lastOutput` → `input` → `""` (presence check,
  not `or`, to preserve falsy-but-legitimate values).
- Builds the model via `build_chat_model(node.data.model or DEFAULT_MODEL, temperature=0.0,
  langsmith_config=get_current_langsmith())`, reusing `guardrails.py`'s existing
  `DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"` constant (imported, not duplicated) —
  both nodes want the same cheap-classifier default.
- Binary mode: schema `class BinaryDecision(BaseModel): result: bool; confidence: float`, calls
  `structured_invoke(llm, messages, schema=BinaryDecision)`.
- Choice mode: schema built dynamically per invocation — `option: Literal[<node's option labels>]`
  + `confidence: float` — via `pydantic.create_model`, since the field's allowed values are node
  config, not known at class-definition time.
- Both modes format `node.data.examples` (if any) into the single user message ahead of the actual
  judgment text, as a numbered few-shot block (`Examples:\n1. Text: "..." → true\n2. ...`), then
  `Now decide:\nText: {state_text}`. This stays inside one `structured_invoke` call — no
  alternating user/assistant example turns, no per-provider prefill handling to worry about.
- Populates `node_results[node.id].output = {"decision": <bool|str>, "confidence": <float>}`.
  Does **not** overwrite `lastOutput` — same transparent-passthrough contract as guardrails, so
  Decision composes with whatever's downstream regardless of which branch was taken.

### Routing (`src/engine/graph_builder.py`)

New `_route_decision(node: DecisionNode) -> Callable[[WorkflowStateDict], str]`, added alongside
`_route_if_else`/`_route_while`/`_route_user_approval` (~line 350). Reads the decision fresh from
`node_results` on each traversal (the router can't re-run the LLM call — unlike if-else's `simpleeval`
re-evaluation, an LLM call isn't free or idempotent enough to repeat during routing) — falls back to
the first branch key on a missing/malformed result, matching the existing "never wedge the graph"
convention.

Branch-key set: binary mode uses the fixed `{"true", "false"}`. Choice mode passes
`{opt.label for opt in node.data.options}` as `required_branches` to `_branch_mapping` — this
already accepts an arbitrary `set[str]` (confirmed by reading `_branch_mapping`'s signature at
graph_builder.py:313), so choice mode's dynamic, node-specific branch set needs **no change** to the
validation function itself — only a new call-site branch in `build_graph`'s node-type dispatch,
same shape as the existing `if"if-else"`/`elif "while"` chain (~line 440).

### Frontend

Six touch points, mirroring if-else's registration exactly:

1. **`frontend/lib/workflow-to-rf.ts`** — pass `decision` nodes through like any other typed node
   (no special-casing needed beyond what already exists for typed node data).
2. **`frontend/components/composer/canvas/node-visuals.ts`** — new `"decision": { icon, ... }`
   entry (icon TBD at implementation time — something distinct from if-else's `GitBranch`).
3. **`frontend/components/composer/canvas/tools-palette.tsx`** — new
   `{ nodeType: "decision", label: "Decision" }` palette entry.
4. **`frontend/components/composer/canvas/workflow-canvas.tsx`** — `BranchingNode` needs a real
   code change here, not just a new table entry: `BRANCH_SPECS` today is a static
   `Record<string, BranchSpec[]>` keyed by node *type*, assuming a fixed branch set per type. For
   Decision's choice mode, the branch set is per node *instance* (whatever options that specific
   node is configured with). `BranchingNode` needs a conditional: for `type === "decision"`, compute
   branches from `data` (`mode === "binary" ? [true,false] : data.options.map(...)`) instead of
   looking up `BRANCH_SPECS`. Binary mode can still use the static table like if-else.
5. **`frontend/components/composer/canvas/node-panels/decision.tsx`** — new panel: mode toggle
   (binary/choice), instruction textarea, model selector (reuse whatever component
   agent/guardrails panels already use), an examples list editor (add/remove rows: input text +
   expected result/option, following the JSON-field stringify/parse pattern from `arcade.tsx` where
   applicable), and — choice mode only — an options list editor (add/remove rows: label +
   description). The zero-shot hint text renders under the examples editor when the list is empty.
6. **`frontend/components/composer/canvas/property-panel.tsx`** — route `decision` node type to the
   new panel component, same dispatch pattern as every other typed node.

## Testing

- **`src/executors/decision.py` unit tests**: binary mode schema + structured_invoke call shape;
  choice mode dynamic schema construction (`pydantic.create_model` produces a `Literal` matching
  the configured options); few-shot example formatting; `lastOutput`/`input`/`""` resolution
  matches guardrails' existing tested behavior; `node_results` output shape; `lastOutput` untouched.
- **`graph_builder.py` test**: choice-mode routing with a 3-option node — confirms
  `_branch_mapping` accepts the dynamic branch set with no changes, and that a malformed/missing
  decision result falls back to the first branch rather than raising.
- **`workflow.py` validation test**: `mode="choice"` with empty/single-entry `options` rejected;
  duplicate option labels rejected.
- **Frontend**: `BranchingNode` component test for the dynamic choice-mode branch count (2 branches
  vs. 4 branches render the right number of handles); `decision.tsx` panel test for the zero-shot
  hint appearing/disappearing as examples are added/removed.
- **Playwright e2e**: one flow — add a binary Decision node, wire both branches, run a workflow,
  confirm the graph takes the correct branch for an unambiguous input.

## Explicitly deferred / out of scope

- Pluggable judgment backend (TypeSafe, Laya, or any non-LLM engine) — LLM-only for now; see the
  standing memory note on revisiting Laya.
- `score` primitive (numeric rubric, threshold-driven branching or plain output).
- Refactoring `guardrails.py` to reuse Decision's binary-judgment code.
- Required/minimum example counts, or any validation gating on example presence.
- Self-consistency / multi-call voting to reduce judgment variance — `temperature=0.0` +
  examples are the only variance-reduction levers in this spec.
- Confidence-threshold-driven routing (e.g. "take the fallback branch if confidence < 0.6") — the
  node returns `confidence` in its output for downstream use, but doesn't act on it itself.
