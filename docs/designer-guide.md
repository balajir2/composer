# Designer Guide

Everything you need to build workflows on the canvas. If this is your first time, start with [getting-started.md](getting-started.md) and the Template gallery — this guide is the reference you come back to.

**Table of contents:**

- [Concepts](#concepts) — Workflows, executions, runs, drafts
- [The canvas](#the-canvas) — Editing nodes, drawing edges, the property panel
- [Variables and references](#variables-and-references) — `{{name}}` substitution and the eval scope
- [Node reference](#node-reference) — All 20 node types
- [Templates](#templates) — The 18 reference workflows and what each demonstrates
- [Publishing workflows](#publishing-workflows) — External invoke API
- [Document uploads](#document-uploads) — PDF / DOCX / Markdown / TXT inputs
- [Patterns and recipes](#patterns-and-recipes) — Common idioms

---

## Concepts

| Term | Meaning |
|---|---|
| **Workflow** | A graph of nodes + edges authored on the canvas. Stored as JSON in Postgres. |
| **Execution** | One run of a workflow against a specific input. Has a unique `executionId`, status (`running` / `completed` / `failed` / `waiting_approval` / `canceled`), and persisted state. |
| **Draft run** | A run started from inside the canvas via the Run Draft button. Uses the unsaved canvas state — useful for iteration. |
| **Production run** | A run started via the published external-invoke endpoint (`POST /api/run/{slug}`) or via the Runs page. Uses the saved workflow definition. |
| **Template** | A workflow flagged `isTemplate=true` + `isPublic=true` that any user can clone via "Use template" on `/designer/templates`. The clone is private and unaffected by template edits. |
| **Public workflow** | `isPublic=true` — anyone in the org can read, but only the owner (or admin) can edit. |
| **Shared workflow** | The owner (or an admin) has granted another user full read+write access via "Manage access." Shared workflows show a "Shared" badge and behave identically to owned ones for the assignee — except delete and owner-transfer, which stay owner/admin-only. |
| **Production workflow** | `isProduction=true` + has an `externalSlug` — callable via `POST /api/run/{slug}` with an API key. |
| **Node alias** | The snake_case form of the node's **Name** field. Available downstream as `{{<alias>}}` (e.g. a node named `Place Picker` is `{{place_picker}}`). |

## The canvas

**Adding a node**: drag from the left palette onto the canvas, or right-click an empty area for the quick-add menu. Each node type has a coloured icon — the colour is consistent across the palette, the canvas chip, and the run-time decoration.

**Drawing edges**: drag from a node's right-hand source handle to the next node's left-hand target handle. Branching nodes (`if-else`, `while`, `user-approval`) have **two** labelled source handles — see [conditional routing](#conditional-routing) below.

**The property panel** opens on the right when you select a node. Every node has a **Name** field — set it. The name becomes the node's alias for prompt references and is what shows up in the run trace, so spend the 5 seconds to give nodes meaningful names. The panel also shows a description-of-this-node-type at the top so you don't have to remember which one does what.

**Save vs Run Draft**: Save persists the canvas state to the workflow row. Run Draft executes the *current canvas state* (which may differ from saved) — useful for iteration. After Run Draft, the canvas decorates each node live: pulsing purple → green (completed) / red (failed). Click any completed node in the result panel to see its input and output.

**Autosave**: the canvas also saves in the background 3 seconds after you stop editing — the same status indicator next to the Save button cycles through *idle → unsaved → saving → saved* (or an error state if the save fails). Autosave doesn't replace clicking Save when you want an immediate write, but it means leaving the tab open with unpersisted edits is no longer a way to lose work. Composer also warns before you close a tab with unsaved changes.

**Sharing access**: workflow owners (and admins) can grant other users full read+write access from the workflow's settings panel ("Manage access") — search by name or email, add or remove assignees. Assignees can open, edit, and run the workflow like the owner; only the owner or an admin can manage who else has access, and only the owner can delete the workflow or transfer ownership.

### Conditional routing

When you connect from an `if-else`/`while`/`user-approval` node, the edge label is auto-set from the source handle (`true`/`false`, `body`/`exit`, `approved`/`rejected`). Both edges are required for the workflow to validate — a half-wired conditional won't save (the backend rejects with "leaves … node but has no branch label").

## Variables and references

Workflow state lives in `state.variables` — a flat dict keyed by name. Every node reads from and writes to it.

**Two reference syntaxes** — both work in most fields:

| Where | Syntax | Engine |
|---|---|---|
| Prompt fields, URLs, headers, free-text values | `{{name}}` or `{{node.field}}` | Substitution: walks `state.variables` by dotted path, JSON-serialises non-strings |
| Conditions (if-else, while), expressions (transform, data-transform) | `{{name}}`, `name`, or `name["field"]` | simpleeval — sandboxed Python; supports arithmetic, comparisons, method calls on strings |

The `{{...}}` Mustache form is rewritten to subscript form (`a["b"]["c"]`) before simpleeval parses it, so designers can use the same syntax everywhere.

**Auto-aliases** — every node's output is automatically available under three names:

```
state.variables["lastOutput"]                 ← whatever the most recent node wrote (clobbered each step)
state.variables["<sanitized_node_name>"]      ← e.g. "place_picker" for a node named "Place Picker"
state.variables["<sanitized_node_id>"]        ← e.g. "agent_1"
state.node_results["<original_node_id>"]      ← full record: status, input, output, timing
```

Reach for the **node-name alias** for readability. `{{lastOutput}}` is fine for one-step handoffs but breaks the moment you insert a new node between source and consumer.

**Reserved names** that user variables cannot shadow: `variables`, `lastOutput`, `node_results`. Names starting with `_` are reserved for engine internals (`_while_iterations`, `_guardrails_result`).

**Useful eval-scope helpers**:

- Functions: `int`, `float`, `str`, `bool`, `len`
- String methods: `.upper()`, `.lower()`, `.strip()`, `.split(...)`, `.replace(...)`, `.startswith(...)`
- List/dict subscripts: `xs[0]`, `obj["key"]`
- Arithmetic + comparisons: `index + 1`, `score > 0.5`, `not flag and other`

What's *not* available: `import`, `eval`, `exec`, list comprehensions, dict literals, set literals, attribute access on user dicts (use subscript: `obj["key"]` not `obj.key`).

## Node reference

The twenty node types, grouped by what they do.

### Flow control

#### `start`

The entry point. Declares **input variables** the runtime form (or external invoke caller) must supply.

| Field | Purpose |
|---|---|
| `inputVariables[]` | Each: `name`, `type` (`text` / `number` / `boolean` / `json` / `document`), `required`, `description`, `defaultValue` |

`document` inputs render as a file picker on the run form; on select the file is uploaded to `/uploads/extract-text` and the extracted plain text becomes the variable value. See [Document uploads](#document-uploads).

When the user submits a JSON object as input, the Start executor **spreads its keys** into top-level variables (so `{"name": "x"}` makes `{{name}}` resolve to `x`). When they submit a string, it lands under `{{input}}`.

#### `end`

Terminates the workflow. The current `lastOutput` becomes `WorkflowExecution.output`. Multiple ends are allowed (e.g. one per branch).

#### `if-else`

Two outgoing handles: `true` / `false`. Routes based on the **Condition** field — a simpleeval boolean expression.

```
{{check_guard.passed}}
not {{check_guard.passed}} and mode == "strict"
{{lastOutput.score}} > 0.7
```

#### `while`

Two outgoing handles: `body` / `exit`. Loops while **Condition** is truthy. Hard cap **Max iterations** (default 10, ceiling 100). The engine maintains `_while_iterations[<node_id>]` automatically so you can write `{{_while_iterations.loop_1}} <= 3`.

Body branch should chain back to the while node to loop. Exit branch fires once when the condition is false (or the cap is hit — which raises `WhileMaxIterationsError`).

#### `user-approval`

Two outgoing handles: `approved` / `rejected`. Pauses execution until a reviewer responds via the runs page. The execution status flips to `waiting_approval` and a row goes into the `approvals` table. Use **Approval message** to tell the reviewer what they're approving.

The reviewer's note (if any) is available downstream as `{{node_results.<id>.output.note}}`.

Set **Approver email** (`approverEmail`) and, optionally, **Approver CC** (`approverCc`) — both support `{{variable}}` substitution — to also notify the reviewer by email the moment the node pauses. The email contains two signed one-click links, Approve and Reject; clicking either resolves the decision directly, with no Composer login required, so an external reviewer with no account can still act. Each emailed link is valid for 72 hours by default (`approval_link_ttl_hours`); after it expires, the in-app Approve/Reject controls on the runs page still work exactly as before — the emailed link is a convenience shortcut, not the only way to decide. Independent of that link TTL, a `waiting_approval` execution that sits with no decision at all for 7 days (168h, `approval_wait_timeout_hours`) is automatically failed by a background sweeper, purely to bound database row growth — a paused execution costs zero compute while waiting (its full state is checkpointed to Postgres), so this outer limit is hygiene, not a resource necessity.

### Compute

#### `agent`

The workhorse. Calls an LLM with optional tool access.

| Field | Purpose |
|---|---|
| `provider` + `model` | Provider dropdown (Anthropic / OpenAI / Google / Groq) + admin-curated model list. Models verified `unavailable` are auto-disabled and won't appear. |
| `instructions` | The user message. Use `{{...}}` for variables. |
| `outputFormat` | `Text` (default) or `JSON`. JSON mode invokes the provider's structured-output API and parses the result. |
| `jsonSchema` | Required when `outputFormat=JSON`. Standard JSON Schema. |
| `selectedTools` | List of qualified tool names (e.g. `tavily.tavily_search`, `firecrawl.firecrawl_scrape`). |
| `mcpServerIds` | List of admin-registered MCP server IDs. |
| `maxIterations` | LLM ↔ tool round-trips before giving up. Default 10, ceiling 100. |
| `includeChatHistory` | Pass the workflow's chat history to the agent. |

The agent loop: invoke → if response has tool calls, run them in parallel and feed `ToolMessage`s back → repeat until response has no tool calls (or cap is hit).

JSON mode + a schema makes downstream `{{<agent_name>.<field>}}` references work via Mustache substitution.

#### `mcp`

Direct MCP server invocation outside the agent loop. Calls a specific tool on a registered MCP server with a prepared argument dict — useful when the LLM-driven agent loop is overkill.

#### `transform`

Run a **simpleeval expression** over state, write the result.

| Field | Purpose |
|---|---|
| `transformScript` | Expression (no return statement — just an expression) |
| `outputKey` | Optional named state variable. When set, the result is written to *both* `lastOutput` AND `state.variables[outputKey]`. Lets compute + persist happen in one node. |

Examples: `{{index}} + 1`, `tickers[{{index}}]`, `len(results)`, `results + [{"id": current_id, "data": lastOutput}]`.

Validation rejects reserved names (`variables`, `lastOutput`, `node_results`) and underscore-prefixed names for `outputKey`.

#### `data-transform`

Map / filter / reduce a list.

| Field | Purpose |
|---|---|
| `operation` | `map` / `filter` / `reduce` |
| `collection` | Expression yielding a list (e.g. `vectorDbResults["results"]`) |
| `expression` | Per-item expression. Item bound to `itemVar` (default `item`). For reduce, `acc` is also bound. |
| `itemVar` | Override the binding name |
| `initial` | Starting value for reduce |

Example: filter chunks above a score threshold — `collection: chunks`, `expression: item["score"] > 0.5`.

#### `set-state`

Write a value into state. Substitutes `{{...}}` in the value before storing.

| Field | Purpose |
|---|---|
| `stateKey` | Variable name to write |
| `stateValue` | Value (any JSON; substitution applies recursively) |

Useful for initialising loop counters / accumulator lists, or passing a literal config to a downstream node.

#### `extract`

LLM-driven structured extraction from messy text. Like `agent` with `outputFormat=JSON` but optimised for the "give me the entities" use case.

| Field | Purpose |
|---|---|
| `input` | Source text (Mustache references upstream output) |
| `jsonSchema` | Schema the extracted JSON must match |
| `model` | Optional model override (defaults to Haiku) |

Common pattern: `http` fetches a JSON API → `extract` pulls a few specific fields → `agent` writes a narrative.

#### `note`

Visual annotation on the canvas. No-op at run time — purely for documentation. Drag onto the canvas, write a markdown comment, leave it there as a sticky-note.

### Integrations

#### `http`

Make an HTTP call.

| Field | Purpose |
|---|---|
| `httpUrl` | URL (Mustache substitution applies) |
| `httpMethod` | `GET` / `POST` / `PUT` / `PATCH` / `DELETE` |
| `httpHeaders` | Dict — values substituted |
| `httpBody` | Optional body (substitution applies recursively to dicts/lists) |
| `responsePath` | Optional dotted path into the JSON response (e.g. `chart.result[0].meta`) — output is just that subtree |

#### `vector-db`

Query OR upsert against Pinecone / Qdrant / Chroma / Weaviate / Milvus.

| Field | Purpose |
|---|---|
| `vectorDbProvider` | Which DB |
| `vectorDbEndpoint` / `vectorDbApiKey` / `vectorDbCollection` | Connection |
| `vectorDbOperation` | `query` (default) or `upsert` |
| `vectorDbQueryPrompt` | (query) Text to embed and search with |
| `vectorDbTopK` / `vectorDbScoreThreshold` | (query) Result tuning |
| `vectorDbDocuments` | (upsert) simpleeval expression yielding chunks — list of `{id?, text, metadata?}`, list of strings, OR a single string |
| `vectorDbChunkSize` / `vectorDbChunkOverlap` | (upsert) Char-window auto-chunking when input is a single string |
| `vectorDbEmbeddingProvider` / `vectorDbEmbeddingModel` / `vectorDbDimension` | Embedding config |

Query output: `{query, results: [{id, score, text, metadata?, vector?}], total, provider, collection, dimension}`.

Upsert output: `{operation, provider, collection, inserted_count, ids, dimension}`. IDs are stable (sha1/uuid5 hashes of chunk text) so re-running the same input overwrites the same vectors instead of duplicating.

#### `join-chunks`

Stitch a list of chunks into a single string. Pairs naturally with `vector-db` query results or any list of text-bearing dicts.

| Field | Purpose |
|---|---|
| `joinChunksVariable` | Name of the state variable holding the chunks (must be a list) |
| `joinChunksSeparator` | Between chunks (default `\n\n`) |
| `joinChunksPrefix` / `joinChunksSuffix` | Around each chunk |
| `joinChunksIncludeMetadata` | Append `[metadata: {...}]` per chunk |

Chunks are read by checking, in order: `chunk["content"]`, `chunk["text"]`, `chunk["page_content"]`, then JSON-stringified as a fallback.

#### `guardrails`

Concurrent LLM-classifier safety filter on the upstream output.

| Field | Purpose |
|---|---|
| `piiEnabled` / `moderationEnabled` / `jailbreakEnabled` / `hallucinationEnabled` | Toggle each check |
| `actionOnViolation` | `block` (raise error) or `warn` (continue, expose verdict) |
| `model` | Optional override (default Haiku) |

Pass-through behaviour: `lastOutput` is **not** overwritten — upstream content keeps flowing. Verdict is exposed via the auto-aliased node handle:

```
{{<node_alias>.passed}}        ← boolean
{{<node_alias>.violations}}    ← list, e.g. ["PII detected"]
{{<node_alias>.message}}       ← human-readable summary
```

Branch on `{{<node_alias>.passed}}` with an `if-else` to deliver-or-redact.

#### `gamma-ai`

Generate a slide deck via [Gamma](https://gamma.app)'s API.

| Field | Purpose |
|---|---|
| `prompt` | Outline / brief / source text |
| `format` | `presentation` / `document` / `social` |
| `textMode` | `generate` (expand outline) / `condense` (summarise source) / `preserve` (verbatim) |
| `numCards` | Approximate slide count |
| `textAmount` | `brief` / `medium` / `detailed` |
| `imageSource` | `aiGenerated` / `unsplash` / `webFreeToUseCommercially` etc. |
| `language` | ISO code |
| `exportAs` | `web` (URL only) / `pptx` / `pdf` |

Polling: 60s initial, 10s poll, 4-min cap. Output includes the Gamma URL and (if `exportAs ∈ {pptx, pdf}`) the download URL.

#### `email`

Send a deterministic workflow email through Resend.

| Field | Purpose |
|---|---|
| `emailProvider` | Provider. Currently `resend`. |
| `emailFrom` | Sender address. Friendly format like `Reports <reports@example.com>` is accepted. |
| `emailTo` / `emailCc` / `emailBcc` | Recipients. Comma, semicolon, and newline separated lists are accepted. |
| `emailReplyTo` | Optional reply-to address. |
| `emailSubject` | Subject line. Mustache substitution applies. |
| `emailBodyType` | `html` or `text`. |
| `emailBody` | Body content. Mustache substitution applies. |
| `emailIdempotencyKey` | Optional Resend idempotency key to avoid duplicate sends on retries. |

Output: `{provider, messageId, to, cc, bccCount, subject, status}`. Use a `user-approval` node before `email` for workflows where a human should review content before distribution.

**Resend sender rules:** the `emailFrom` domain must be verified in the Resend account and allowed by the API key. For example, if Resend has verified `example.com`, use `Reports <reports@example.com>` as the sender. The recipient can be Gmail/Outlook/etc.; the sender cannot be `gmail.com` unless that domain is verified in your Resend account, which normal users cannot do.

**Recommended key type:** use a Resend **Sending access** key restricted to the verified sending domain. In Admin → LLM keys, Composer may report "Resend sending-only key accepted; domain listing is restricted." That is a successful least-privilege check, not a delivery error. Full-access keys are only needed for broader Resend administration outside Composer.

#### `arcade`

Per-user OAuth-mediated tool calls (Google Docs, Slack, etc.) via [Arcade](https://arcade.dev).

| Field | Purpose |
|---|---|
| `arcadeTool` | Tool ID (e.g. `Google.CreateDocument`) |
| `arcadeInput` | Argument dict (substitution applies) |
| `arcadeUserId` | Per-user identifier (typically `{{input.user_id}}`) |

If the user hasn't authorised Arcade for the requested scope, the executor pauses with an interrupt — the runs page shows an "Authorise" button. Once they finish OAuth in Arcade's UI, click Resume; execution continues. Reuses the same interrupt machinery as `user-approval`.

#### `jira`

Agentic Jira Cloud access (create, search, update, transition, and comment on issues) via REST API v3. Unlike the shared MCP/tool-provider credentials used elsewhere, Jira credentials are entered directly on the node — per-node, per-workflow, no shared connection required.

| Field | Purpose |
|---|---|
| `domain` | Jira Cloud domain (e.g. `your-org.atlassian.net`). |
| `email` | Jira Cloud account email used for Basic auth. |
| `apiToken` | API token from [id.atlassian.com/manage/api-tokens](https://id.atlassian.com/manage/api-tokens). |
| `instructions` | Prompt describing what the node should do; the LLM picks which Jira tool(s) to call. |
| `model` | Optional `provider/modelId` override; falls back to a default model. |
| `maxIterations` | Cap on the tool-call ↔ LLM-response loop (default 10, hard ceiling 100). |

**Credential storage:** the API token is encrypted at rest (AES-256-GCM) the moment the workflow is saved, and every API response redacts it to a fixed `••••••••` marker — the plaintext or ciphertext never leaves the server after the initial save. The designer's Jira panel treats an unchanged, redacted field as "keep the existing token"; typing a new value replaces it. The token is only decrypted in-memory, server-side, at execution time.

Output: `{lastOutput}` — the model's final text response after any tool calls complete.

## Templates

The 18 templates seeded by `scripts/seed_templates.py`. Open the gallery at [/designer/templates](http://localhost:3000/designer/templates).

| # | Name | Demonstrates | External deps |
|---|---|---|---|
| 1 | Simple Agent | Start → Agent → End — basic Q&A | LLM key |
| 2 | Web Research Agent | Tavily-tooled agent | LLM + Tavily |
| 3 | Scrape and Summarise | Firecrawl + chained agents (`{{lastOutput}}` handoff) | LLM + Firecrawl |
| 4 | Guardrails + Branching | Guardrails pass-through + `{{<node>.passed}}` branching | LLM only |
| 5 | Multi-Company Stock Analysis | Multi-source HTTP synthesis (Yahoo Finance) | LLM only |
| 6 | Yahoo Finance Stock Report | HTTP → Extract → Agent narrative | LLM only |
| 7 | Amazon Product Research | Firecrawl scrape → Extract → Recommendation | LLM + Firecrawl |
| 8 | Human-in-the-Loop Approval | user-approval + branched approve/reject | LLM only |
| 9 | Zillow Property Finder | Firecrawl + Extract + data-transform filter + Agent | LLM + Firecrawl |
| 10 | While-loop with Accumulator | Real iterating loop with `transform.outputKey` | LLM only |
| 11 | RAG (Vector DB + Join Chunks) | Query a vector DB, stitch chunks, RAG answer | LLM + vector DB + OpenAI embeddings |
| 12 | Document Ingestion (Vector DB Upsert) | Upload PDF/DOCX/MD/TXT → chunk → embed → upsert | LLM + vector DB + OpenAI embeddings |
| 13 | Meeting Transcript to Action Items | Document upload → JSON extraction → email drafting | LLM only |
| 14 | Customer Support Triage | Classify-and-branch — pure-LLM, no external deps | LLM only |
| 15 | Gamma AI Presentation Generator | Tavily research → outline → gamma-ai slides | LLM + Tavily + Gamma |
| 16 | Code Review Assistant | Agent JSON + guardrails on output + branched delivery | LLM only |
| 17 | Lead Enrichment | Multi-source research + structured CRM JSON | LLM + Tavily + Firecrawl |
| 18 | Approved Email Distribution | Agent drafts HTML → human approval → Resend email delivery | LLM + Resend |

Templates are owned by `userId=null` so no one can edit them through the API — clicking "Use template" creates a private user-owned copy. Edit the seed script + re-run to update the gallery.

## Publishing workflows

Three steps to make a workflow callable as `POST /api/run/{slug}`:

1. **Settings → Publish** — pick a URL-safe slug (e.g. `my-workflow`). The slug is globally unique. Publishing flips `isProduction=true`.
2. **Generate an API key** at [/runs/api-keys](http://localhost:3000/runs/api-keys). The plaintext `ck_...` value shows once.
3. **Call the endpoint**:
   ```bash
   curl -X POST "https://composer.bounteous.com/api/run/my-workflow" \
     -H "Authorization: Bearer ck_YOUR_KEY" \
     -H "Content-Type: application/json" \
     -d '{"input": {"question": "..."}, "sync": true, "timeoutSeconds": 90}'
   ```

**Authz**: Public workflows are callable by any valid API key. Private workflows are callable only by the owner's keys (or an admin's key).

**Async by default**: Returns `{executionId, workflowId, status: "running", streamUrl}`. Connect to the `streamUrl` WebSocket for live events. Pass `"sync": true` to wait inline (max 300s); on timeout the call falls back to async shape.

The endpoint URL is shown on the canvas top bar (and on the Settings page) once published, with a one-click copy button.

## Document uploads

Start node inputs with `type: "document"` render as a file picker on the run form. Supported: `.txt`, `.md`, `.markdown`, `.pdf`, `.docx`, plus `.rst` and `.log`. Hard cap **10 MB**.

On select, the file is sent to `POST /uploads/extract-text` which extracts plain text in-memory and returns it. The form stashes the text as the input variable's value — downstream nodes see a regular string and reference it as `{{<variable_name>}}` like any other text input.

No persistent storage. The file's bytes are gone after extraction. For workflows that need to keep the source around, hook up an HTTP node to fetch from your own storage.

The natural ingest pipeline (Template 12) is **Start (file picker) → vector-db upsert (auto-chunks the extracted text)** — three nodes total. The vector-db executor's char-window chunking is opinionated but cheap; for production you'd typically pre-chunk upstream and pass a list to vector-db instead of a string.

## Patterns and recipes

### Compute and persist in one node

`transform.outputKey` collapses what used to be `transform → set-state` into a single node. Use it for loop counters, accumulators, and any "compute X and store as Y" step.

```
transform script:    {{index}} + 1
outputKey:           index
```

### Real loops with accumulators

Template 10 is the canonical reference. Pattern:

```
set-state index=0  →  set-state results=[]  →  while (index < len(items))
                                                ├─body─→ … work … → transform: results + [...]  → outputKey=results
                                                │                  → transform: index + 1       → outputKey=index
                                                │                  → (loop back to while)
                                                └─exit─→ final agent reads {{results}}
```

### Classify and branch

Template 14 is the cleanest reference. Pattern: agent in JSON mode emits a classification → if-else condition is `{{<classifier>.<some_boolean>}}` → specialist agents on each branch.

For multi-category routing, chain if-else nodes (binary routing only is built in; nested if-else covers any tree).

### Output-side guardrails

Template 16: place guardrails *after* an agent that handles untrusted input. Catches PII / credentials the agent would otherwise surface verbatim. Branch on `{{<guardrails>.passed}}` to deliver vs redact.

### Designer reference convention

When you create a new node, **set the Name field**. The snake_case form is the alias every downstream prompt uses. Without a name you're stuck with `{{node_id}}` (the auto-generated UUID-ish string), which makes prompts brittle to canvas edits.
