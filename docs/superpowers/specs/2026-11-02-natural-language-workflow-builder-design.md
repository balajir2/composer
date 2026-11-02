# Design: Natural-language workflow builder (Builder agent)

**Date:** 2026-11-02
**Status:** In design — pre-implementation. Awaiting user review before planning.
**Origin:** User request — "describe a flow in English and an AI agent creates the flow, asks for
credentials if needed, then the user tests it for correctness; the agent is also available if there
is an error and helps the user."

---

## Why this spec exists

Composer's workflow authoring today is exclusively manual: a designer drags nodes onto the React
Flow canvas (`frontend/components/composer/canvas/workflow-canvas.tsx`), configures each node in the
property panel, and wires edges by hand. This is a high-friction path for anyone who can describe a
workflow in prose but doesn't know the 26-node palette intimately.

This spec adds a conversational **Builder**: an AI agent that turns a natural-language description
into a valid, runnable Composer workflow on the same canvas, prompts for the credentials the flow
needs through the *existing* setup flows (never collecting secrets inline), and — when a test run
fails — diagnoses the failure and proposes a patch the user can approve before it lands.

The output is ordinary `nodes[]` + `edges[]` JSON validated by the exact same Pydantic +
`validate_workflow_shape` path as a hand-built workflow, so everything downstream (execution,
publishing, autosave, sharing) works unchanged. The builder is a **generation-time assistant**, not
a new runtime: it produces workflows, it does not participate in workflow execution.

---

## Decisions locked by user Q&A during brainstorming

These are the product-level constraints agreed before design, and this spec treats them as fixed:

- **The experience lives inside the designer.** A chat panel beside the existing React Flow canvas
  (`frontend/app/designer/[workflowId]/page.tsx`), not a separate page or a standalone chat that
  hands off later. The agent's output lands on the *same* canvas the user already edits.
- **Plan → approve → build, with stage-granularity checkpoints.** The agent first emits a plan of
  logical stages for the user to approve or edit; on approval it lays down nodes/edges in *stages*,
  pausing for confirmation at each stage boundary — not per-node (chosen over the more tedious
  per-node confirmation), and not one-shot-regenerate-everything.
- **Credentials are never collected inline in chat.** When the plan requires a credential, the agent
  emits a `credential_request` event; the frontend resolves it by opening the *existing* setup flow
  for that credential type (MCP server create/authorize, vector-DB key fields, Jira/Confluence
  credentials, Google Drive OAuth, Resend). Secrets keep living in their existing encrypted stores
  (`McpServer`, per-node encrypted fields, `CloudStorageConnection`, `LlmApiKey`), and the agent
  never echoes a secret value.
- **Error assistance is auto-diagnose + proposed patch, user-approved.** After a failed run the
  agent reads the failure context itself (failed node, error, `node_results`, variables), explains
  it, and proposes a concrete node/edge patch. The user reviews a before/after diff and approves
  before anything is applied. No silent auto-fix.
- **The build conversation is ephemeral.** Chat history, plan cards, and patch history live only in
  the browser and are lost on reload. There is **no new conversation/session table**. The *workflow*
  itself is persisted normally (it is a normal workflow row), but the transcript is not.
- **Approach: tool-calling builder agent** (chosen over one-shot structured-output regeneration and
  over modeling the builder as its own LangGraph state machine). The agent is a single LLM loop —
  mirroring `src/executors/agent.py`'s existing hand-rolled tool-call loop — armed with a small set
  of builder tools that each make one discrete, auditable, validatable change.

---

## Components

### Backend module: `src/builder/` (new)

| File | Responsibility |
|---|---|
| `builder_agent.py` | The agent loop. Given a turn (message + transcript + optional `execution_id`), binds the builder tools, iterates tool-call → result (bounded, like `agent.py`'s max-iterations), and returns typed events + a final reply + the updated workflow. **Stateless between turns** — see "Turn model" below. |
| `builder_tools.py` | LangChain `StructuredTool` definitions: `plan_workflow`, `create_node`, `update_node`, `remove_node`, `connect_nodes`, `set_start_inputs`, `request_credential`, `diagnose`, `propose_patch`. Each returns a structured result; invalid mutations return a `WorkflowValidationError` message as the tool *result* so the model can self-correct. |
| `node_catalog.py` | Builds a compact LLM-friendly catalog of all node types (type, purpose, key fields, credential dependencies) by reflecting over `src/engine/workflow.py`'s Pydantic models. Injected into the system prompt every turn so the agent knows the valid vocabulary. |
| `diagnostics.py` | Assembles a diagnosis context from an `execution_id` (reads `db.workflowexecution`, extracts `status`, `error`, `nodeResults`, a bounded tail of `variables`). Redacts secrets before the context reaches the model. |
| `layout.py` | Dependency-free deterministic layered layout (topological depth → column/row with fixed spacing). Advisory only — positions are ordinary `Position{x,y}` values the user can drag afterward. |
| `prompts.py` | System-prompt templates (builder, planner, patcher personas) + the node-catalog splice. |

### Turn model and transport (stateless server)

The conversation is ephemeral, so the server holds **no** session state between turns — this is
both the simplest way to honor "no persistence" and the correct choice for the Cloud Run topology
(multiple instances; a subsequent turn may land on a different instance, so in-memory session state
would be unsafe). The browser owns the transcript:

- Each `POST /builder/{workflowId}/message` carries `{ message, transcript?, execution_id? }`.
  `transcript` is the compact client-held history (user/assistant text + the structured plan and
  patch proposals the agent previously emitted) so the agent has multi-turn context. `execution_id`
  is set when the user is asking for help with a specific failed run.
- The server reconstructs agent context from **transcript + current workflow row (read from DB) +
  node catalog**. It does not need to re-fetch or replay anything else.

The workflow state itself *is* persisted — at **turn boundaries**. Within a turn the agent may make
several tool calls whose intermediate graph states are invalid (a node placed before it is
connected). The server applies deltas to an in-memory copy and, at end of turn, validates the result
with `validate_workflow_shape`; only a valid result is committed. This deliberately sidesteps the
autosave-vs-partial-build problem: builder edits are committed by the builder endpoint, while the
user's subsequent *manual* edits continue to flow through the existing 3s-debounced autosave
(`frontend/lib/use-autosave.ts` → `PUT /workflows/{id}`).

### API: `src/api/builder.py` (new)

- `POST /builder/{workflowId}/message` — request `{ message, transcript?, execution_id? }`; response
  `{ events: BuilderEvent[], reply: str, workflow: WorkflowRead }`.
  - `events` is a list of a discriminated `BuilderEvent` union:
    - `plan` — `{ stages: [{ id, summary, credential_requirements?: ... }] }`
    - `stage_applied` — `{ stage_id, summary, node_count, edge_count }`
    - `credential_request` — `{ kind, node_id, detail, resolvable_by: "user" | "admin" }`
    - `patch_proposal` — `{ diff: { added_nodes, removed_nodes, changed_nodes, added_edges, removed_edges }, explanation }`
    - `message` — plain assistant prose
    - `error` — a failed turn (structured, standard error shape)
  - `workflow` is the (possibly updated) workflow, returned through the same `_to_workflow_read`
    redaction path `src/api/workflows.py` already uses, so secrets never reach the client.
- Auth mirrors `workflows.py`: `Depends(get_current_role)` + the same owner/assignee authorization
  check `update_workflow` already performs (role admin, or `userId` match, or `_has_assignment`).
- Rate-limited via the existing `enforce` helper with a new
  `rate_limit_builder_per_minute` setting (new entry in `src/config.py`, defaulting conservatively —
  e.g. 20/min — because each turn can burn several LLM calls).

### Persistence of builder edits

The builder endpoint reuses `src/api/workflows.py`'s write path rather than duplicating it: it
mutates the workflow in memory, calls `validate_workflow_shape` + `_check_workflow_size_limits`,
runs the `_encrypt_jira_tokens` / `_encrypt_vector_db_keys` / `_encrypt_http_headers` /
`_encrypt_confluence_tokens` helpers against the existing row (preserving redacted-marker
semantics), and persists via `db.workflow.update`. This is the single most important correctness
decision in the spec: builder-generated workflows are indistinguishable from hand-built ones, and
every existing secret-encryption/redaction guarantee applies automatically.

### Model configuration

The builder model is resolved through `src/llm/providers.py`'s `build_chat_model` — no new provider
work. The model string is read from a new `DeploymentSetting` key (`builder_model`) in the existing
`deployment_settings` table, with a code-level default; unset → fall back to the agent node's
default model. This lets an admin steer the builder to a strong model without a redeploy, consistent
with how other admin-managed settings work. Structured output for the `plan`/`propose_patch` tools
uses `src/llm/structured_output.py`'s `structured_invoke`.

### Frontend: `frontend/components/composer/builder-chat/` (new)

- `builder-panel.tsx` — the collapsible/resizable chat panel mounted in the designer page; owns the
  ephemeral transcript + event list.
- Card components: assistant text; **plan card** (stages with status, Approve/Edit); **credential
  request card** (button → opens the existing flow); **patch-diff card** (before/after with
  Approve/Reject).
- `frontend/lib/api/builder.ts` — typed client for `POST /builder/{workflowId}/message`, mirroring
  `frontend/lib/composer-api.ts`.
- **Canvas integration:** node/edge results from the `workflow` response are applied to the same
  React Flow state `workflow-canvas.tsx` already renders — generated nodes are ordinary, draggable,
  editable nodes. The designer page (`frontend/app/designer/[workflowId]/page.tsx`) hosts the panel
  alongside the canvas.

### Credential resolution map

The `request_credential` event carries a `kind`; the frontend resolves each `kind` to the existing
flow. The `resolvable_by` field distinguishes per-user vs deployment-level:

| kind | resolvable_by | Existing flow |
|---|---|---|
| `mcp-server` | user | `create-mcp-dialog.tsx` (new static-auth server) or `mcp-authorize-button.tsx` (OAuth) |
| `vector-db` | user | the node's property-panel key fields (`vectorDbApiKey`/`vectorDbEmbeddingApiKey`) |
| `jira` / `confluence` | user | the node's property-panel domain/email/apiToken fields |
| `google-drive` | user | existing Google Drive OAuth flow (`src/api/cloud_storage_oauth.py`) |
| `resend` (email) | admin | message: "requires a Resend API key configured by an admin" |
| `tool-provider` (Tavily/Serper/Firecrawl/Browserless/Gamma/Arcade) | admin | message: deployment-level key (admin configures) |
| `llm-provider` | admin | message: model/key is admin-managed (`LlmApiKey`); agent should instead pick an `enabled` model |

For per-user `kind`s, the agent should *prefer* an already-configured resource (an existing MCP
server, an existing Google Drive connection) over requesting a new one; the `diagnose`/`plan` tools
surface the currently-available resources so the agent can reference them.

---

## Key sequences

### Sequence A — build

1. User types a description in the chat panel.
2. `POST /builder/{id}/message` (`{ message }`). Server reads the workflow, injects the node catalog,
   runs the agent → emits a `plan` event + a prose summary, returns control. No workflow mutation.
3. Frontend renders the plan card with Approve/Edit. (Edit lets the user annotate stages before
   approving.)
4. User approves → `POST /builder/{id}/message` (`{ message: "approve", transcript: [plan…] }`).
5. Agent applies stage 1 (several `create_node`/`connect_nodes` tool calls), validates at stage end,
   persists, emits `stage_applied` + the updated `workflow`. Frontend renders the new nodes on the
   canvas and asks for the next confirmation.
6. Repeat per stage. At any point a `credential_request` may appear; the frontend shows the card and
   opens the relevant existing flow; once resolved, the user says "done" and the agent continues.
7. When the flow is complete, the user runs it through the existing inline-test path.

### Sequence B — test & fix

1. User runs the workflow (existing `POST /executions` + `designer-execution-panel.tsx`); it fails
   (`ExecutionRead.status = "failed"`, populated `error` + `nodeResults`).
2. User clicks "ask builder" (or types into the panel) with the failed `execution_id` attached.
3. `POST /builder/{id}/message` (`{ message, execution_id }`). Agent calls `diagnose(execution_id)`,
   reads the redacted failure context, explains it, and emits a `patch_proposal` (concrete diff).
4. Frontend renders the patch-diff card; user **Approves** → next turn applies the patch (validates +
   persists, emits `stage_applied`). **Rejects** → agent stands down.

---

## Error handling

- **Tool-level:** a `create_node`/`update_node`/`connect_nodes` call that yields an invalid graph
  returns `WorkflowValidationError` text as the tool result, so the agent self-corrects within its
  iteration budget; it is not surfaced to the user as a hard failure.
- **Turn-level:** if the agent exhausts its iteration budget without reaching a valid, savable state,
  the turn returns an `error` event with the standard structured error shape and the workflow is left
  untouched (the in-memory copy is discarded; nothing partial is committed).
- **Validation is the contract:** the builder never produces a graph that bypasses
  `validate_workflow_shape`, the 100-node/200-edge caps, or the secret-encryption path — because it
  persists through `workflows.py`'s logic, it inherits all of those guarantees.

## Security

- **Runs as the authenticated user.** Same `get_current_role` + owner/assignee authorization as
  `update_workflow`; cross-user access returns 404 (not 403) per ADR-0021.
- **No secret generation or echo.** The agent's only interaction with secrets is emitting
  `credential_request` referencing existing stores. `diagnostics.py` redacts before the model sees
  run state, and the response workflow is redacted via `_to_workflow_read`.
- **Generated `http` nodes** are subject to the same SSRF guard as hand-built ones at execution time
  (the guard lives in the HTTP executor, not the builder).
- **Prompt-injection posture.** User natural language is untrusted input, but the agent's action
  space is bounded to node/edge mutations that must pass schema + shape validation; it cannot execute
  arbitrary code or reach outside the workflow schema at build time. No filesystem or shell tools are
  exposed to the builder.
- **Cost/abuse.** New per-user rate limit on the builder endpoint (above); each turn is iteration-
  bounded, mirroring `agent.py`.

## Testing

- **`src/builder/` unit tests:** each tool's happy path + invalid-input path (mocked DB); catalog
  generation covers all 26 node types; layout determinism (same input → same positions); diagnostics
  redacts secrets and bounds `variables`; the agent loop's iteration cap and self-correction path
  (mocked LLM emitting a bad `create_node` then a corrected one).
- **Endpoint tests:** `POST /builder/{id}/message` authz (owner/assignee/admin allowed, other → 404);
  a build turn persists a valid workflow through the encryption helpers; an invalid end-of-turn
  state is discarded; rate-limit enforcement.
- **Agent tests with mocked LLM:** deterministic tool-call sequences for plan→approve→build and
  diagnose→propose→apply, mirroring the existing executor test pattern.
- **Frontend:** Vitest for plan/credential/patch-diff cards and the transcript reducer; Playwright
  e2e for one build→run→fix loop.
- **Regression:** builder-generated workflows pass the existing `validate_workflow_shape` and secret
  redaction tests unchanged.

## Backward compatibility / open-source fork safety

- **No database migration.** No new table is introduced (the conversation is ephemeral). The only
  data-model touchpoint is a new `DeploymentSetting` *row* (`builder_model`), which is data, not
  schema. `prisma/schema.prisma` is unchanged.
- **Purely additive.** New `src/builder/` module + new `src/api/builder.py` router + new frontend
  components. `src/api/workflows.py`, `src/engine/graph_builder.py`, and `src/engine/workflow.py` are
  consumed as-is (the builder *calls* `validate_workflow_shape`, it does not modify it). No existing
  endpoint's contract changes.
- **No new dependency.** LangChain tools + `structured_invoke` + `build_chat_model` are all already
  in the project; layout is hand-rolled (no dagre). `pyproject.toml` is unchanged.
- **Feature-off by default.** The builder is inert until the chat panel is opened; the
  `rate_limit_builder_per_minute` and `builder_model` settings default safely. A deployment that
  never configures a model simply gets the existing default-model behavior.

## Explicitly deferred / out of scope

- Streaming the agent's token/step output in real time (v1 is turn-based; the existing WS/SSE
  patterns are a later enhancement).
- Persisting the build conversation or exposing it across sessions.
- Auto-test-until-green loops (the builder diagnoses only after the *user* runs a test).
- Voice or other input modalities; multi-workflow generation in one prompt.
- A "describe → template" entry point outside the designer (landing page, templates gallery).
- Any change to OAB (`D:/GitHub/open-agent-builder`) — per the frozen-reference rule, none is made.
