# Design: Decision node — judgment-based branching for the palette

**Date:** 2026-09-22
**Status:** Approved — implementation plan not yet written
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
- **`JudgmentProvider` abstraction, ships now — reversed from the original "no abstraction" call.**
  The executor talks to a small provider interface, not directly to `build_chat_model`/
  `structured_invoke`. One implementation ships (`LLMJudgmentProvider`, wrapping Composer's existing
  LLM stack exactly as the earlier no-abstraction draft described); TypeSafe/Laya remain unwired,
  but adding either later is a new provider class + registry entry, not a refactor of the node,
  executor contract, or frontend schema. This is a deliberate reversal of the earlier YAGNI call —
  the user judged the interface cheap enough, and the future-optionality valuable enough, to build
  ahead of a second real implementation.
- **The provider choice is user-visible now, and TypeSafe ships as a real second option —
  reversed again from "deferred."** The Designer panel's provider selector shows two choices:
  "LLM" and "TypeSafe (Jev)". This is a deliberate re-reversal: the spike (see the standing memory
  note) found no case for TypeSafe in Guardrails and pricing is still unpublished — that finding
  stands, but the user chose to build the integration anyway rather than wait. Flagged once here
  for the record; not a blocker.
- **TypeSafe is reachable from the Decision node only — never from Agent, Extract, Guardrails, or
  any other node that takes a free-form `model` string.** Jev isn't a chat model (no free-form
  generation, no tool use); plugging it into `build_chat_model` — the shared dispatcher every other
  `model`-string field ultimately flows through — would silently break its contract for every
  caller. The isolation is enforced in two places, both by omission, not by a runtime check:
  - **Backend:** `src/llm/providers.py`'s `_build_raw_chat_model` dispatches on an explicit
    `if provider == "anthropic"` / `"openai"` / `"google"` chain (confirmed by reading the file —
    lines 78+). No `"typesafe"` branch is added, ever. A stray `"typesafe/jev-latest"` string
    reaching `build_chat_model` from anywhere fails loudly (unrecognized provider) rather than
    silently doing something wrong — that failure is the safety net, not a bug to fix later.
  - **Frontend:** each node panel with a model dropdown hardcodes its own `PROVIDER_OPTIONS` array
    (confirmed in `agent.tsx:14` — it's per-panel, not a shared/imported constant).
    `agent.tsx`/`guardrails.tsx`/`extract.tsx` (and any other panel with a model field) are **not
    touched** by this spec — "typesafe" is added to exactly one provider list: the new
    `decision.tsx` panel's own selector.
  Admin-side setup (key CRUD, activation, verify — the "Admin LLM catalog" section below) *is*
  identical to the other four providers, per the user's explicit ask — that's purely
  administrative and grants no reachability from any node by itself. The restriction is about
  which node *executors* can ever construct a call to TypeSafe, not about hiding it from admins.
- **TypeSafe examples are folded into `criteria` text, not true few-shot.** TypeSafe's API has no
  per-example (input→output pair) mechanism — only a static `criteria` description per option (or
  per true/false). `TypeSafeJudgmentProvider` formats `examples` into that description as
  illustrative lines. This is a best-effort approximation, not equivalent to the LLM provider's
  real few-shot conversation turns, and is documented as a known limitation rather than presented
  as equivalent behavior across providers.
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
    provider: str | None = None   # judgment backend name; defaults to "llm" at executor time


class DecisionNode(BaseModel):
    id: str
    type: Literal["decision"]
    position: Position
    data: DecisionNodeData
```

Validation: `mode="choice"` requires a non-empty `options` list (≥2 entries, unique labels);
`mode="binary"` ignores `options`. Enforced in `DecisionNodeData` via a `model_validator`, mirroring
how other node-data classes in this file validate cross-field invariants.

### Judgment provider abstraction (`src/llm/judgment.py`)

New file. Mirrors the existing `Executor`/`register_executor`/`build_executor` pattern in
`src/executors/base.py:1-85` — same shape, applied one layer down (a *provider* a node's executor
calls, not the node's executor itself):

```python
@runtime_checkable
class JudgmentProvider(Protocol):
    async def decide_binary(
        self, *, instruction: str, examples: list[DecisionExample], text: str, model: str | None,
    ) -> tuple[bool, float]: ...   # (result, confidence)

    async def decide_choice(
        self, *, instruction: str, options: list[DecisionOption],
        examples: list[DecisionExample], text: str, model: str | None,
    ) -> tuple[str, float]: ...    # (chosen option label, confidence)


_REGISTRY: dict[str, type[Any]] = {}

def register_judgment_provider(name: str): ...   # decorator, same shape as register_executor
def build_judgment_provider(name: str) -> JudgmentProvider: ...   # raises on unknown name
```

`LLMJudgmentProvider` (`register_judgment_provider("llm")`) is the only implementation shipped in
this spec. It contains exactly the logic the original (pre-reversal) draft of this spec put
directly in the executor:

- Builds the model via `build_chat_model(model or DEFAULT_MODEL, temperature=0.0,
  langsmith_config=get_current_langsmith())`, reusing `guardrails.py`'s existing
  `DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"` constant (imported, not duplicated) —
  both nodes want the same cheap-classifier default.
- `decide_binary`: schema `class BinaryDecision(BaseModel): result: bool; confidence: float`, calls
  `structured_invoke(llm, messages, schema=BinaryDecision)`.
- `decide_choice`: schema built dynamically per invocation — `option: Literal[<option labels>]`
  + `confidence: float` — via `pydantic.create_model`, since the allowed values are caller-supplied,
  not known at class-definition time.
- Both methods format `examples` (if any) into the single user message ahead of the judgment text,
  as a numbered few-shot block (`Examples:\n1. Text: "..." → true\n2. ...`), then
  `Now decide:\nText: {text}`. Stays inside one `structured_invoke` call — no alternating
  user/assistant example turns, no per-provider prefill handling to worry about.

`TypeSafeJudgmentProvider` (`register_judgment_provider("typesafe")`) ships alongside
`LLMJudgmentProvider` in this spec:

- Plain HTTP via `httpx` (already a project dependency — no new package) against
  `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <TYPESAFE_API_KEY>`. No
  LangChain integration exists for TypeSafe, so this doesn't go through `build_chat_model`.
- `decide_binary` → one `noul` question: `{"type": "noul", "instructions": instruction, "criteria":
  {"true": ..., "false": ...}}` (examples folded into the `criteria` strings per the note above).
  Response's `answers.<id>.noul` (0.0–1.0) is thresholded at ≥0.5 for `result`, and used directly as
  `confidence` (distance from 0.5, normalized — exact formula at implementation time).
- `decide_choice` → one `choice` question: `{"type": "choice", "instructions": instruction,
  "criteria": {<option label>: <description, with folded-in examples>, ...}}`. Response's
  `answers.<id>.choice` is the option label directly; `answers.<id>.confidence` is used as-is.
- `model` param is passed through as TypeSafe's `model` field when set, else omitted (API defaults
  to `jev-latest`). **Bare model id (`"jev-latest"`), never a `"typesafe/..."`-prefixed string** —
  unlike `LLMJudgmentProvider`, which passes `node.data.model` straight to `build_chat_model` and
  therefore needs Agent's `"<provider>/<modelId>"` convention, `TypeSafeJudgmentProvider` never
  touches `build_chat_model` at all, so there's no prefix to parse. This is the concrete form of
  the isolation boundary above: the two providers' `model` strings are different formats on
  purpose, and normalizing them to look the same would be the mistake, not an improvement.
- Network/HTTP errors surface as a `JudgmentProviderError` (new exception, raised by both
  providers on failure) — the executor doesn't need to know which provider it's talking to to
  handle a failure.

A future `LayaJudgmentProvider` would implement the same two methods against its own backend and
register under its own name — no change to `DecisionNodeData`'s shape (just a new valid value for
`provider`), the executor, or the frontend panel beyond adding the name to the selector's option
list.

### Admin LLM catalog — TypeSafe as a first-class provider

The admin already has a full per-provider surface for the four LLM providers (key CRUD, a
"test connection" probe, and a separate `LlmModel` catalog with per-model "verify" + enable/disable
— the mechanism behind "Admin → LLM keys" and the recent "OpenAI model-verify probe" fix). TypeSafe
gets the same four touchpoints, not a special case:

1. **API key CRUD** (`src/config.py`, `src/security/key_sync.py`, `src/cli/keys.py`,
   `src/api/admin_llm_keys.py`) — the four-touchpoint pattern every existing provider key uses:
   - `src/config.py` — new `typesafe_api_key: str | None = None` field on `Settings`.
   - `src/security/key_sync.py` — add `"typesafe": "typesafe_api_key"` to
     `PROVIDER_TO_SETTINGS_FIELD` (line 41), so an admin-entered key synced from `llm_api_keys` at
     boot populates `Settings` the same way every other provider's does.
   - `src/cli/keys.py` — add `"typesafe": "TYPESAFE_API_KEY"` to `_PROVIDER_TO_ENV` (line 15), so
     `composer keys sync --target vercel` pushes it to the cloud deployment env like the others.
   - `src/api/admin_llm_keys.py` — add `"typesafe"` to `_ALLOWED_PROVIDERS` (line 20). No new
     admin-UI code path — the admin frontend's LLM keys screen already renders whatever
     `_ALLOWED_PROVIDERS` allows.
   - `.env.example` — new `TYPESAFE_API_KEY=` entry in the LLM providers block, with a provisioning
     comment (`https://console.typesafe.ai/keys`) matching the existing four providers' format.
2. **Key test-connection** (`src/api/admin_llm_keys_test.py`) — new `_test_typesafe(key)`,
   registered in `_TESTERS` (line 260): minimal `noul` question against a trivial state, same
   auth-check-only shape as the other testers.
3. **Model catalog + verify probe** (`src/api/admin_llm_models.py`,
   `src/api/admin_llm_models_verify.py`) — this is the "activate / get the active model" surface.
   `admin_llm_models.py`'s `LlmModelCreate`/CRUD endpoints are already provider-agnostic (no
   `_ALLOWED_PROVIDERS`-style gate on `provider` — confirmed by reading the file), so an admin can
   already `POST /admin/llm-models {"provider": "typesafe", "modelId": "jev-latest"}` today with no
   code change to seed the catalog row and toggle `enabled`. The one real addition is a verify
   probe: new `_verify_typesafe(model_id, key)` in `admin_llm_models_verify.py`, registered in
   `_VERIFIERS` (line 253) — `POST /v1/systemone` with a single trivial `noul` question against
   `model_id`, classified the same way the existing verifiers are (200 → `ok`, 401/403 →
   `auth_error`, 404/model-shaped 4xx → `unavailable`).
4. **Designer/Decision-panel model dropdown** (`src/api/llm_models_live.py`) — `_PROVIDERS` (the
   live-`/models`-fetch spec table) is not extended for TypeSafe: it has no `/models` discovery
   endpoint to call live. Instead, `"typesafe"` is added to a new small `_DB_ONLY_PROVIDERS` set
   that `list_available_models` checks before `_fetch_live` — when a provider is in that set, it
   skips straight to `_db_fallback(db, provider)`, which is the module's existing, already-tested
   fallback path (today only reached on live-fetch failure for the other four providers). This
   means the admin-curated `LlmModel` rows (from point 3) are the only source of TypeSafe models in
   the dropdown — there's no live catalog to reconcile against, so nothing to probe-and-filter the
   way `_probe_invocable` does for the other providers.

`TypeSafeJudgmentProvider` reads `get_settings().typesafe_api_key` at call time — same access
pattern `build_chat_model` already uses for the other providers' keys. The Decision panel's model
selector, when `provider="typesafe"`, calls `GET /llm-models/available?provider=typesafe` exactly
like it does for `provider="llm"` — same component, same request shape, not a special case.

### Executor (`src/executors/decision.py`)

New file, structured like `if_else.py` (produces the audit row; the router closure in
`graph_builder.py` does the actual routing), but thin — it resolves input and dispatches to the
provider rather than calling the LLM stack itself:

- Resolves input text the same way guardrails does: `lastOutput` → `input` → `""` (presence check,
  not `or`, to preserve falsy-but-legitimate values).
- `provider = build_judgment_provider(node.data.provider or "llm")`.
- Calls `provider.decide_binary(...)` or `provider.decide_choice(...)` depending on `node.data.mode`.
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
same shape as the existing `if "if-else"`/`elif "while"` chain (~line 440).

**"Conditional source" is a hardcoded type set in four places, not one — all four need
`"decision"` added, or the change breaks silently rather than cleanly:**
`CONDITIONAL_SOURCE_TYPES` (graph_builder.py:310, module-level), `_conditional_types` (line 291,
inside `validate_workflow_shape`'s branch-label check), the normal-edge-skip check (line 428,
`if source_node.type in {"if-else", "while", "user-approval"}`), and — this one is easy to
miss because it's in a different file entirely — **`frontend/lib/workflow-to-rf.ts:26`**, which
has its own `CONDITIONAL_SOURCE_TYPES` Set gating whether a drawn edge's `sourceHandle` gets saved
as `branch` at all. Missing that one means every edge drawn from a Decision node saves with
`branch: null` and the backend rejects it with "has no branch label" — a bug that only shows up
when someone actually uses the Designer, not in a backend-only test pass.

### Frontend

Six touch points, mirroring if-else's registration exactly:

1. **`frontend/lib/workflow-to-rf.ts`** — add `"decision"` to the module's own
   `CONDITIONAL_SOURCE_TYPES` Set (line 26). This is required, not optional — see the routing
   section above for why skipping it breaks edges drawn from a Decision node.
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
5. **`frontend/components/composer/canvas/node-panels/decision.tsx`** — new panel, and the *only*
   panel with "TypeSafe (Jev)" in a provider list: mode toggle (binary/choice), a provider selector
   (two options — "LLM" and "TypeSafe (Jev)" — wired to `data.provider`, defaulting to `"llm"`), a
   model selector that calls `GET /llm-models/available?provider=<data.provider>` for both — same
   endpoint Agent/Guardrails use, parameterized by whichever provider is selected (for `"typesafe"`
   that's always the DB-curated `jev-latest`-style rows an admin added, since there's no live
   discovery). **The write format branches on provider**, matching the backend split above: LLM
   mode writes `"<provider>/<modelId>"` into `data.model` (Agent's existing convention, since it
   flows to `build_chat_model`); TypeSafe mode writes the bare `modelId` (no prefix — it never
   reaches `build_chat_model`). An examples list editor (add/remove rows: input text + expected
   result/option, following the JSON-field stringify/parse pattern from `arcade.tsx` where
   applicable — shows a small note when provider is `"typesafe"` that examples are folded into
   criteria text, not true few-shot), and — choice mode only — an options list editor (add/remove
   rows: label + description). The zero-shot hint text renders under the examples editor when the
   list is empty.
   `agent.tsx`, `guardrails.tsx`, `extract.tsx`, and every other panel with a `PROVIDER_OPTIONS`
   array are unmodified by this spec — confirming that at implementation time is part of the review,
   not just an intent stated here.
6. **`frontend/components/composer/canvas/property-panel.tsx`** — route `decision` node type to the
   new panel component, same dispatch pattern as every other typed node.

## Testing

- **`src/llm/judgment.py` unit tests**: `build_judgment_provider("llm"/"typesafe")` returns the
  right class; unknown provider name raises; `LLMJudgmentProvider.decide_binary`'s schema +
  `structured_invoke` call shape; `decide_choice`'s dynamic schema construction
  (`pydantic.create_model` produces a `Literal` matching the passed-in options); few-shot example
  formatting; `TypeSafeJudgmentProvider`'s request body shape for both `noul` and `choice`
  questions (mocked HTTP), examples-folded-into-criteria formatting, response parsing (`noul`
  threshold, `choice`/`confidence` passthrough), and `JudgmentProviderError` on HTTP failure.
- **Isolation regression test**: `build_chat_model("typesafe/jev-latest", ...)` raises (no
  `"typesafe"` branch in `_build_raw_chat_model`) — this is the one test in the suite whose whole
  job is to fail loudly if a future change accidentally wires TypeSafe into the shared LLM
  dispatcher. A frontend test asserting `"typesafe"` is absent from `agent.tsx`/`guardrails.tsx`/
  `extract.tsx`'s `PROVIDER_OPTIONS` arrays would be the equivalent guard on the UI side.
- **Admin catalog unit tests**: `_test_typesafe` (mocked HTTP, auth-check shape);
  `_verify_typesafe` (mocked HTTP, `ok`/`unavailable`/`auth_error`/`error` classification matching
  the existing `_classify` buckets); `list_available_models(provider="typesafe")` skips
  `_fetch_live` and goes straight to `_db_fallback` via the new `_DB_ONLY_PROVIDERS` set;
  `POST /admin/llm-models` with `provider="typesafe"` succeeds with no code change (confirms the
  existing provider-agnostic CRUD assumption this spec relies on).
- **`src/executors/decision.py` unit tests**: `lastOutput`/`input`/`""` resolution matches
  guardrails' existing tested behavior; dispatches to `decide_binary`/`decide_choice` per
  `node.data.mode`; `node_results` output shape; `lastOutput` untouched.
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

## Backward compatibility / open-source fork safety

Composer is now open-sourced with active forks, so this section makes explicit what was checked
to keep this change purely additive — nothing here should require a fork maintainer to do more
than a clean merge:

- **No database migration, at all.** `Workflow.nodes`/`Workflow.edges` are `Json` columns
  (`prisma/schema.prisma:32-33`) — node types are validated at the Pydantic layer, not the SQL
  layer. Adding `DecisionNode`/`DecisionNodeData` is a new variant in a discriminated union with
  no `prisma migrate` step, no `ComposerBootstrap`-style placeholder concerns, nothing that can
  fail or need rollback at the DB level. `LlmApiKey.provider` / `LlmModel.provider` are already
  free `String` columns (no enum/check constraint), confirmed by reading both tables — adding
  `"typesafe"` rows is data, not schema.
- **Every code touchpoint is an addition, not a modification, of existing behavior.** Checked
  against the actual files, not assumed: `graph_builder.py` gets a new `_route_decision` function
  and a new `elif` branch — the existing `if-else`/`while`/`user-approval` branches are untouched.
  `workflow.py` gets new classes inserted near `IfElseNode` — no existing class changes.
  `key_sync.py`/`cli/keys.py`/`admin_llm_keys.py`/`admin_llm_models_verify.py`/`llm_models_live.py`
  each get one new dict/set entry (`PROVIDER_TO_SETTINGS_FIELD`, `_PROVIDER_TO_ENV`,
  `_ALLOWED_PROVIDERS`, `_TESTERS`, `_VERIFIERS`, the new `_DB_ONLY_PROVIDERS`) — no existing
  provider's entry is touched, and no existing endpoint's request/response contract changes.
  `workflow-canvas.tsx`'s `BranchingNode` gains a conditional for `type === "decision"`; the
  existing `BRANCH_SPECS` lookup path for if-else/while/user-approval is unchanged.
- **No new dependency.** `httpx` (for `TypeSafeJudgmentProvider`'s HTTP calls) and `pytest-httpx`
  (for mocking it in tests) are both already declared in `pyproject.toml` — confirmed by reading
  it, not assumed from general familiarity with the stack.
- **New config is opt-in with a safe default.** `typesafe_api_key: str | None = None` — a
  deployment (fork or otherwise) that never sets `TYPESAFE_API_KEY` sees no behavior change
  anywhere; the field simply stays `None` and nothing references it outside
  `TypeSafeJudgmentProvider`, which nothing calls unless a workflow author explicitly sets
  `provider="typesafe"` on a Decision node.
- **The isolation boundary (previous section) is itself a fork-safety property**, not just a
  scoping decision: because TypeSafe never enters `build_chat_model`, a fork that has its own
  Agent/Extract/Guardrails customizations can't be broken by this change touching shared LLM
  dispatch code — that dispatch code isn't touched at all.

## Explicitly deferred / out of scope

- A `LayaJudgmentProvider` (or any other non-LLM, non-TypeSafe engine) — the interface ships with
  `llm` and `typesafe` wired; see the standing memory note on revisiting Laya before building it.
- `score` primitive (numeric rubric, threshold-driven branching or plain output) — TypeSafe's
  `score` primitive is unused here for the same reason it was deferred for the LLM provider: no
  concrete use case yet beyond the two branching shapes.
- Refactoring `guardrails.py` to reuse Decision's binary-judgment code.
- Required/minimum example counts, or any validation gating on example presence.
- Self-consistency / multi-call voting to reduce judgment variance — `temperature=0.0` +
  examples are the only variance-reduction levers in this spec.
- Confidence-threshold-driven routing (e.g. "take the fallback branch if confidence < 0.6") — the
  node returns `confidence` in its output for downstream use, but doesn't act on it itself.
