# Composer — Decisions Log (ADRs)

Append-only log of material engineering decisions. Every decision that isn't self-evident from the code gets an entry here so future-us (and future IE reviewers) can reconstruct the reasoning.

**Format per entry:**
- **Status** — Proposed / Accepted / Superseded-by-ADR-NNNN
- **Context** — the problem and what made it non-obvious
- **Decision** — the choice, stated crisply
- **Consequences** — what this obligates or forecloses
- **Implemented by** — commit hashes / PRs
- **Related** — other ADRs, specs, external docs

Entries are numbered sequentially and never renumbered. Superseding decisions get a new number and link back.

---

## ADR-0001: LangGraph checkpointer — Prisma owns schema, thin custom saver

**Status:** Accepted (2026-04-20)

**Context.** LangGraph Python persists execution state (for interrupts, resume, time-travel) through a `BaseCheckpointSaver` implementation. Two offs-the-shelf options existed — the official `langgraph-checkpoint-postgres` package (psycopg-based, manages its own tables), or writing a custom saver over our Prisma client. Three alternatives considered:

- **A.** Use `langgraph-checkpoint-postgres` as-is; Prisma owns everything else. Cleanest if you don't care about schema unification.
- **B.** Write a full custom Prisma-backed saver. Single ORM.
- **C.** Prisma owns the checkpoint tables in `schema.prisma`, but we implement a thin `BaseCheckpointSaver` subclass that reads/writes them via the Prisma client.

**Decision.** Option **C**. `schema.prisma` declares `LangGraphCheckpoint` and `LangGraphCheckpointWrite` with the shape required by `BaseCheckpointSaver` (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint bytes, metadata bytes, and a writes table keyed by task_id/idx/channel). A small `PrismaCheckpointSaver` class implements the four async methods (`aget_tuple`, `alist`, `aput`, `aput_writes`) using Prisma queries. The LangGraph default serializer (`JsonPlusSerializer`) produces the bytes.

**Consequences.**
- All migrations go through `prisma migrate dev` — single source of truth for schema, aligned with IE's expectation of Prisma-first data modeling.
- We are on the hook for keeping the saver current if LangGraph's checkpointer interface evolves. The interface is small (4 methods); the risk is bounded.
- We do not take a dependency on `langgraph-checkpoint-postgres` and thus don't carry its psycopg transitive.
- If Prisma's BYTEA handling or async driver proves problematic, the escape hatch is to switch to Option A mid-phase (the schema would migrate to LangGraph-managed tables; non-trivial but recoverable).

**Implemented by.** Phase 1 (commits `df25733`..`5e13c19` on `main`, plus a follow-up cleanup commit for code-review findings).

**Related.** ADR-0002, ADR-0004.

---

## ADR-0002: Workflow JSON schema — full OAB fidelity in Phase 1

**Status:** Accepted (2026-04-20)

**Context.** Composer stores workflows as JSON (persisted in the `Workflow.nodes` and `Workflow.edges` columns) and needs Pydantic models to validate them. How strict/complete those models are in Phase 1 is a scope question:

- **A.** Minimal: only parse `{nodes: [{id, type}], edges: [{source, target}]}` in Phase 1; extend per phase.
- **B.** Faithful to OAB: port all 18 node-type `data` models as Pydantic classes in Phase 1, even though executors for 16 of them don't exist yet.
- **C.** Hybrid: validate the envelope (id/type/position/edge shape) but leave `data` as `dict[str, Any]` per-type until the owning phase lands.

**Decision.** Option **B**. Phase 1 defines Pydantic models for all 18 node types (`start`, `end`, `note`, `agent`, `mcp`, `if-else`, `while`, `user-approval`, `transform`, `data-transform`, `set-state`, `extract`, `http`, `guardrails`, `vector-db`, `gamma-ai`, `arcade`, `join-chunks`), with the `type` field as a `Literal` discriminator per the OAB TypeScript types at `lib/workflow/types.ts`. A workflow with an unshipped executor validates successfully at CRUD time; attempting to *execute* it raises `NotImplementedError(f"Executor for node type {type!r} lands in Phase N")` when the graph builder tries to instantiate that executor.

**Consequences.**
- Workflow JSON contract is stable from Phase 1. Later phases only add executor code, not schema.
- Costs ~1 additional day in Phase 1 for the 16 data-model classes whose executors don't exist yet. Acceptable per the "no shortcuts, build as a product" preference.
- If OAB's type definitions are ambiguous for a given node type's `data` fields, the implementation reads `lib/workflow/types.ts` (and the executor's usage of those fields) as the authoritative specification.
- OAB node-type discriminator strings (`if-else`, `user-approval`, `gamma-ai`, etc.) are preserved verbatim in JSON; Python class names normalize to snake_case (`IfElseNodeData`, `UserApprovalNodeData`, `GammaAiNodeData`).

**Implemented by.** Phase 1 (commits `df25733`..`5e13c19` on `main`, plus a follow-up cleanup commit for code-review findings).

**Related.** ADR-0001, ADR-0005.

---

## ADR-0003: Documentation runs in lockstep with development

**Status:** Accepted (2026-04-20)

**Context.** Composer is a ~13-week rebuild where dozens of non-obvious decisions will be made. If docs lag the code, the strategic value of being able to hand Composer to IE as a proposal-ready module evaporates — nobody, including future-us, will reconstruct *why* things are the way they are.

**Decision.** Four living documents, each with a defined scope and update cadence:

| Doc | Path | Cadence |
|---|---|---|
| Master design | [`docs/design/2026-04-20-composer-python-port-design.md`](./2026-04-20-composer-python-port-design.md) | Amended when a decision invalidates a section |
| Phase design specs | `docs/superpowers/specs/YYYY-MM-DD-phase-N-<topic>-design.md` | Written before a phase begins; re-committed mid-phase if scope shifts |
| Decisions log (ADRs) | `docs/design/decisions.md` (this file) | Append-only; new entry ships in the commit that implements it |
| Changelog | [`CHANGELOG.md`](../../CHANGELOG.md) | Updated in the same commit as the change |

**Enforcement rules.**
1. **Doc-with-code:** a commit that changes behavior must include the matching doc update. PRs without matching spec/decision/changelog updates are rejected.
2. **Phase-entry:** before writing code for a phase, its spec file exists and is committed.
3. **Phase-exit:** the "phase N complete" commit updates `CLAUDE.md`'s phase-status table.
4. **ADR anchors:** every ADR has a stable `## ADR-NNNN:` heading so code comments and other specs can link to it.

**Consequences.**
- More commits touching docs. Accepted cost.
- Future maintainers (Balaji in 6 months, IE reviewers, new engineers) can recover intent without reading the full git history.
- Spec drift is structurally prevented — not policed by memory.

**Implemented by.** This file (new); [CHANGELOG.md](../../CHANGELOG.md); [CLAUDE.md](../../CLAUDE.md) references.

**Related.** All future ADRs.

---

## ADR-0004: Local dev Postgres — Neon only

**Status:** Accepted (2026-04-20), supersedes the "Neon primary, Docker fallback" guidance shipped in Phase 0.

**Context.** The master design doc originally specified "Neon for dev, Docker fallback." Phase 0 shipped `docker-compose.yml` and dual-path `.env.example` / README. Revisited at Phase 1 entry.

**Decision.** **Neon only** for local development. No `docker-compose.yml` committed. `.env.example` documents the expected Neon URL format. CI uses a GitHub Actions Postgres service container for integration tests (not a checked-in compose file).

**Consequences.**
- `docker-compose.yml` is removed.
- `.env.example` simplified to Neon-shaped URL only.
- README's "Offline fallback" section removed; replaced with a one-line pointer to Neon's free tier.
- Integration tests (`@pytest.mark.integration`) require a `TEST_DATABASE_URL` env var set to a Neon dev branch or an equivalent Postgres. CI provides this via GitHub Actions `services: postgres`; developers running integration tests locally either point at a scratch Neon branch or skip the marker.
- If Neon is down during development, the user is unblocked because only *integration* tests require Postgres — unit tests (which are the bulk) run with fakes.

**Implemented by.** Phase 1 (commits `df25733`..`5e13c19` on `main`, plus a follow-up cleanup commit for code-review findings).

**Related.** ADR-0001 (checkpointer needs Postgres to exercise).

---

## ADR-0005: Phase 1 API surface + authentication

**Status:** Accepted (2026-04-20)

**Context.** Phase 1's design-doc deliverable is "POST a minimal workflow JSON, execute it, retrieve the execution record via API." Considered three scopes — minimal (3 endpoints), faithful workflow-CRUD subset (8 endpoints), or full OAB parity with stubs. Authentication scope (JWT middleware from day 1 vs deferred to Phase 7) was a secondary question.

Key context that resolved the tradeoff: **no one is using Composer today**. Progressive API surface per phase has no consumer. The full REST contract crystallizes naturally as Phases 2–6 add executors; pinning it in Phase 1 is a guess that will be wrong.

**Decision.**

- **API surface in Phase 1 = 3 endpoints:**
  - `POST /workflows` — create a workflow from JSON; returns created row
  - `POST /executions` — `{workflowId, input}` → creates execution row, schedules background task to run the workflow, returns execution row with `status: "running"`
  - `GET /executions/{execution_id}` — returns the execution row (status, output, error, node results)

  These are the minimum needed to exercise the Phase 1 engine end-to-end. Workflow CRUD (LIST/UPDATE/DELETE), approvals, OAuth, test-connection, upload, and streaming endpoints land in their owning phases (3, 5, 7).

- **Authentication in Phase 1 = no middleware; JWT primitives as library code only.**
  - `src/security/jwt.py` ships now with `create_access_token()`, `verify_access_token()`, typed payload models, and full unit-test coverage.
  - No FastAPI dependency wired into routes. All Phase 1 endpoints are anonymous; `userId` defaults to `"dev"`.
  - Phase 7 wires the primitives into a `Depends()` guard on all routes. At that point the primitives are tested and stable — Phase 7 just assembles them.

**Consequences.**
- Phase 1 stays within its design-doc deliverable; no scope creep from Phases 3/5/7.
- The "no shortcuts" principle is honored by building JWT primitives fully (not stubbing them) and by building all 18 node-type Pydantic models (ADR-0002).
- Anonymous API in Phase 1 is safe because Composer has no external users and runs only on local dev machines during the backend-rebuild window. Phase 7 restores the security posture before any external exposure.
- The 3 Phase 1 endpoints' response shapes are best-effort — they may be revised in Phase 7 as part of API-parity work. This is acceptable because no one depends on them in the interim.

**Implemented by.** Phase 1 (commits `df25733`..`5e13c19` on `main`, plus a follow-up cleanup commit for code-review findings).

**Related.** ADR-0002, ADR-0003.

---

## ADR-0009: Tool Provider Framework — unified ToolProvider abstraction for standard tools and MCP servers

**Status:** Accepted (2026-04-20)

**Context.** ADR-0006 decided to pull the Phase 3 agentic-loop architecture and one Phase 6 standard tool (Tavily) into Phase 2. That raised the follow-on question: what's the abstraction for *tools* that both standard integrations (Tavily, Serper, Firecrawl, …) and MCP servers (Highspot, Notion, …) implement? Three candidates:

- **A.** A loose `ToolFactory` protocol with a `register_tool_factory` function. Each integration follows a convention but nothing enforces shape. (Initial sketch in the Phase 2 spec draft.)
- **B.** A formal framework — `ToolProvider` ABC, `AuthRequirement` taxonomy, `ToolDefinition` descriptors, `resolve_tools_for_node` entry point, `list_providers()` for discovery. Each integration is a one-file `@register_tool_provider` subclass.
- **C.** `importlib.metadata` entry-point based plugin system — external packages can declare providers without touching the main codebase.

**Decision.** Option **B**. Ship the full framework in Phase 2:

- `src/tools/base.py` — `ToolProvider` ABC, `AuthRequirement` hierarchy (`NoAuth`, `ApiKeyAuth`, `OAuthAuth`), `ToolDefinition`, `BuildContext`, `HealthStatus`.
- `src/tools/registry.py` — `register_tool_provider` decorator, `get_provider`, `list_providers`, `resolve_tools_for_node`.
- `src/tools/providers/tavily.py` — the Phase 2 reference implementation.
- `src/mcp/` directory created as a placeholder. Phase 3 populates it with `McpToolProvider(ToolProvider)` plus one provider instance per registered MCP server.

**Consequences.**
- Phase 2 ships ~120 lines of framework + ~80 lines of Tavily provider instead of ~30 lines of "registry + one tool" code. Net ~+1 day in Phase 2.
- Phase 3 shrinks: MCP becomes "subclass `ToolProvider` + OAuth + one Prisma table per user-side token storage," not "design a tool registration system."
- Phase 6 integrations (Serper, Firecrawl, Browserless, Gamma, Arcade) each cost ~0.5 day instead of ~1 day — same template, drop a file in `providers/`.
- Phase 10 (UI) gets `list_providers()` and `provider.tools()` for free — the workflow editor's tool picker is a direct API call over the framework.
- If external plugin discovery (Option C) becomes valuable later, it's a non-breaking migration: plugin authors still subclass `ToolProvider`, the only change is how the registry finds them (switch from "import for side effects" to `importlib.metadata.entry_points`).
- The `auth: AuthRequirement` declaration flows into the Phase 10 UI as a "you need to set X to enable this" indicator, and into the Phase 1 JWT/auth plumbing as per-provider authorization checks in Phase 7.

**Implemented by.** Phase 2 (commits `252c80d`..`36c5c4f` on `main`).

**Related.** ADR-0006, ADR-0002 (node-type models — `selectedTools`, `mcpServerIds`, `mcpTools` fields consumed here).

---

## ADR-0006: Agent executor — LangChain chat models, custom 10-iteration loop, pull Phase 3/6 hooks forward

**Status:** Accepted (2026-04-20)

**Context.** OAB's Agent executor (`lib/workflow/executors/agent.ts`, 1289 lines) uses raw provider SDKs (`@anthropic-ai/sdk`, `openai`, `@langchain/google-genai`) and implements a custom `while iterations < 10` loop that invokes the model, extracts tool calls, executes them in parallel, feeds results back, and caps at 10 iterations. The master design doc scoped Phase 2 as "Agent node **without tools** — multi-LLM provider support." Three axes needed a decision:

1. **Chat-model layer:** raw provider SDKs (match OAB) vs LangChain abstractions (langchain-anthropic, langchain-openai, langchain-google-genai, langchain-groq are already installed per Phase 0 stack).
2. **Agentic loop scope:** no-tools only (strict to design doc) vs loop skeleton now with hooks filled later vs full tools (scope creep).
3. **Tool set coverage in Phase 2:** zero tools vs full OAB standard-tool set (Tavily + Serper + Firecrawl + Browserless) vs one representative tool.

**Decision.**

- **Chat-model layer: LangChain.** Every provider call goes through the `langchain-{provider}` chat model. `chat_model.bind_tools([...])` handles the Anthropic `tool_use`/OpenAI `function` format divergence internally — no manual format dispatch like OAB has. This is `Option B` from the Q4 brainstorm.
- **Agentic loop: custom `while iter < MAX_ITERATIONS=10` loop.** Wraps LangChain's `model.ainvoke(messages)` with our own iteration cap, usage accumulation, and tool-execution dispatch. Does **not** use LangChain's `AgentExecutor`/`create_tool_calling_agent` — those hide the iteration cap and make it hard to thread LangSmith config + Phase 3 MCP tools + Phase 5 interrupt semantics in a controlled way.
- **Phase 2 tool coverage: Tavily only (standard-tool side) + tool-execution branch wired into the loop.** Scope pulled forward:
  - **Phase 3 agentic-loop architecture** → Phase 2. The full while-loop lands now, with a `ToolRegistry` protocol that MCP tools (Phase 3) plug into by registering themselves. Phase 3's diff becomes "implement `McpToolResolver.resolve(node)` → register `Tool` instances" rather than "rewrite the Agent executor to add a loop."
  - **One Phase 6 standard tool (Tavily)** → Phase 2. Proves the tool-registration and tool-execution paths end-to-end with a real external API. Phase 6 adds Serper / Firecrawl / Browserless / Gamma / Arcade by registering their Tool wrappers — no executor changes.

**Consequences.**
- Phase 2 ships ~600 lines of executor code (loop + provider dispatch + tool binding + variable substitution + JSON output) instead of the ~200 a literal interpretation of the design doc would produce.
- ~3 extra days in Phase 2. Phase 3 becomes ~2 days shorter (MCP is tool-registration + OAuth, not executor rebuild). Net schedule impact is neutral.
- All provider-specific reasoning-model detection (o1/o3/gpt-5 use `max_completion_tokens`) lives in one place: a `model_param_adapter` that Phase 2 gets right once.
- LangSmith integration threads through LangChain's native callback system (explicit config pass, per the six MCP fixes' Lesson 6 about threading LangSmith config rather than relying on env vars).
- OAB's 1289-line executor compresses to ~400 lines in Python because LangChain handles the provider-format dispatch OAB did by hand. This is a real code-quality win.

**Implemented by.** Phase 2 (commits `252c80d`..`36c5c4f` on `main`).

**Related.** ADR-0002 (node-type models), ADR-0007, ADR-0008.

---

## ADR-0007: Variable substitution — full OAB `{{...}}` parity

**Status:** Accepted (2026-04-20)

**Context.** OAB's variable substitution engine (`lib/workflow/variable-substitution.ts`) supports multiple `{{...}}` syntaxes and is used by every node that interpolates runtime state into a configured string (Agent `instructions`, HTTP `body`/`headers`, Transform `script`, Extract `prompt`, etc.). Three options:

- **A.** Flat keys only — just `{{variable_name}}` → `state.variables[name]`. Simplest impl.
- **B.** Full OAB parity — `{{input}}`, `{{nodeId.field}}`, `{{state.variables.path.to.value}}`, with prototype-pollution guard on path walks.
- **C.** Jinja2 — more powerful (loops, filters) but syntactically diverges from OAB.

**Decision.** Option **B**. Port the full OAB engine faithfully:
- `{{pattern}}` regex: `/\{\{([^}]+)\}\}/g` in JS → `re.compile(r"\{\{([^}]+)\}\}")` in Python.
- Flat keys resolve to `state.variables[key]`.
- Dotted paths walk `state.variables` (and `state.nodeResults` when Phase 3+ uses that).
- `state.variables.<path>` and `state.nodeResults.<path>` explicit prefixes supported.
- Prototype-pollution guard blocks `__proto__`, `constructor`, `prototype` keys at every path segment. Python equivalent: a `_UNSAFE_KEYS = {"__class__", "__dict__", "__globals__"}` set plus the JS-style names (since ported OAB workflow JSON might reference them).
- Unresolved placeholders render as literal `{{name}}` (matching OAB's fall-through behavior) rather than raising — this lets templates reference not-yet-populated fields without blowing up execution.

**Consequences.**
- Phase 2 lands a standalone `src/variable_substitution.py` module + unit tests; every future executor that interpolates strings imports `substitute(template, state)`.
- OAB workflows that use `{{nodeId.field}}` round-trip through Composer unchanged — no template rewriting required during migration.
- Security posture matches OAB (blocks prototype-pollution paths); subject to Phase 8 security-review rescoping of whether the OAB guard is sufficient.
- Jinja-style features (loops, conditionals) are **not** supported. If someone authors `{% for x in y %}` in a template, it renders as literal text, same as OAB.

**Implemented by.** Phase 2 (commits `252c80d`..`36c5c4f` on `main`).

**Related.** ADR-0002, ADR-0006.

---

## ADR-0008: Structured output — LangChain `with_structured_output`, shared primitive for Agent + Extract

**Status:** Accepted (2026-04-20)

**Context.** When an Agent node sets `outputFormat: "JSON"`, OAB does a naive `JSON.parse()` on the response text and falls back to the raw string on parse failure. Phase 2 has to decide whether to match OAB's naive approach or use LangChain's `with_structured_output()` which leverages provider-native JSON / structured-output modes (OpenAI `response_format`, Anthropic structured output, Google JSON mode).

Phase 4's Extract node will also need structured output, so the decision has downstream impact.

**Decision.** Use LangChain's `with_structured_output` as a **hybrid**, and factor it into a shared primitive both Agent (Phase 2) and Extract (Phase 4) use:

- **`src/llm/structured_output.py`** — module that wraps the `with_structured_output` call. Phase 2 adds it; Phase 4's Extract node imports it.
- **Agent behavior:**
  - `outputFormat == "Text"` → return response text as-is.
  - `outputFormat == "JSON"` + `jsonSchema` provided → `chat_model.with_structured_output(jsonSchema).ainvoke(messages)`. Provider enforces schema at generation time.
  - `outputFormat == "JSON"` + no `jsonSchema` → `chat_model.with_structured_output(method="json_mode")` for OpenAI/Anthropic (they support schema-less JSON mode); for Google, fall back to "ask in prompt, `json.loads` the response" pattern.
- **Extract behavior (Phase 4):** always has a schema; always uses the schema-enforced path. Extract's executor becomes a thin orchestration wrapper over the same primitive.

**Consequences.**
- Deliberate deviation from OAB's naive behavior. Flagged in CHANGELOG as an OAB→Composer divergence. OAB regression tests that asserted specific "JSON that failed to parse → raw string" behavior may need adaptation.
- Phase 4 Extract node becomes smaller (uses `structured_output` helper) rather than reimplementing.
- Provider-native structured output is more reliable than prompt-based JSON asks — same-quality output, fewer retries, fewer silent failures.
- Pulls a piece of Phase 4 work into Phase 2. Consistent with ADR-0006's decision to pull the full agentic loop forward.

**Implemented by.** Phase 2 (commits `252c80d`..`36c5c4f` on `main`).

**Related.** ADR-0002, ADR-0006.

---

## ADR-0010: MCP Tool Provider — resolver-side instantiation

**Status:** Accepted (2026-04-20)

**Context.** Phase 2's Tool Provider Framework (ADR-0009) ships `@register_tool_provider` as a class decorator that instantiates a provider once at import time. That pattern works for standard tools (Tavily, Serper, etc.) whose Python class and configuration are both known at code-load time. MCP is different: MCP servers are DB rows added at runtime via REST. Each server has its own URL, auth config, and tool set. The same `McpToolProvider` Python class, instantiated many times with different rows.

Two ways to integrate MCP:

- **A.** Instantiate in the resolver. `resolve_tools_for_node` detects `mcp_server_ids` on the node, fetches McpServer rows, directly instantiates `McpToolProvider(server)` on the fly. Registry stays static-tool-only.
- **B.** Extend the registry with dynamic runtime registration. Add `register_runtime_provider(instance)` alongside `@register_tool_provider`. When an MCP server is added via REST, the create handler instantiates and registers. Requires re-registration on every app startup (lifespan scans Prisma).

**Decision.** Option **A**. MCP and standard tools have fundamentally different lifecycles — one is code-shipped-at-startup, the other is user-created-at-runtime. Mixing them in the same registry bends the framework to accommodate a semantic mismatch. Keep the registry clean for static tools; let the resolver dispatch on node config:

- `selectedTools: list[str]` → registry (Phase 2 behavior, unchanged)
- `mcp_server_ids: list[str]` → Prisma lookup + `McpToolProvider(row)` instantiation + `provider.tools()` + `provider.build_tool()`

`McpToolProvider` still **implements** `ToolProvider` (inherits the ABC), just isn't decorator-registered. UI enumeration of "MCP servers the user has connected" is a separate Prisma query, not `list_providers()`.

**Consequences.**
- Registry stays small and single-purpose (static tools only).
- No need for per-startup Prisma scanning or registry re-hydration.
- MCP-specific REST endpoints (`POST /mcp-servers`, etc.) are independent of the tool registry code path — they only touch Prisma.
- Phase 10 UI that enumerates providers will have to query both the registry (`list_providers()`) AND Prisma (`McpServer.find_many`) — two calls, two lists, joined in the UI. Acceptable cost.
- Phase 3b's OAuth flow slots in as additional methods on `McpToolProvider` + new Prisma tables, not as changes to the registry.

**Implemented by.** Phase 3a (commits `16bb663`..`2fd3961` on `main`, 2026-04-20).

**Related.** ADR-0002 (node types include `mcp` with `mcp_server_ids` field), ADR-0009 (tool provider framework).

---

## ADR-0011: OAuth tokens server-side + service-account fallback for shared MCP servers

**Status.** Accepted.
**Date.** 2026-04-20.

**Context.** Phase 3b adds OAuth MCP servers (Highspot is the canonical target). Three atomic decisions had to be made before writing code:
1. Where do tokens live — client-side (e.g., cookies) or server-side (Prisma)?
2. What happens when a user can see a **shared** OAuth MCP but has no personal token for it?
3. Do we encode the RFC 8707 `resource` parameter everywhere it's required, or only where a specific IdP demands it?

**Decision.**

1. **Tokens live server-side only, encrypted with `src/security/encryption.py`.** The API response shape never includes ciphertext or plaintext tokens — only `hasAccessToken: bool`. The client triggers authorize/disconnect via REST; the server exchanges, stores, refreshes, and decrypts on demand inside `McpToolProvider._build_auth_header_async`.
2. **Service-account token fallback for shared servers.** When a user accesses a shared MCP but has no personal `McpOAuthToken` row for it, `oauth.get_valid_access_token` falls back to the server *owner's* token. This lets teams register one OAuth MCP per org and share it without requiring every user to go through their own OAuth flow.
3. **RFC 8707 `resource` parameter everywhere.** Every outbound OAuth call (authorize URL, token exchange, refresh, client_credentials) includes `resource = derive_resource(server.url)`. Unit tests assert this on all four paths. Highspot rejects token exchange without it; other IdPs may start enforcing it in the future.

**Alternatives considered.**
- Client-side tokens via encrypted cookies. Rejected — client becomes a token custody point, tokens appear in logs, cross-site scripting becomes a token-leak vector.
- Per-user tokens only, no fallback. Rejected — forces every user in a team to OAuth-connect to every MCP. OAB telemetry showed under-use of shared MCPs as a result.
- Emit `resource` only for Highspot. Rejected — the IdP list changes over time; hard-coding provider sniffing is a maintenance trap.

**Consequences.**
- `McpToolProvider` grows a `db + user_id` dependency (injected at resolver time). The resolver already has these; the change is local.
- The UI can't inspect stored tokens even via dev tools — by design. Debugging a broken connection requires looking at `McpOAuthToken.expiresAt` + error fields (not exposed in 3b; add in Phase 10 UI).
- The service-account fallback creates an implicit delegation. Documented in the endpoint's OpenAPI description and in `oauth.get_valid_access_token`'s docstring.

**Implemented by.** Phase 3b (commits `b80f88f`..`a66479d` on `main`, 2026-04-21).

**Related.** ADR-0010 (MCP resolver-side instantiation), ADR-0009 (tool provider framework), [Phase 3b spec](../superpowers/specs/2026-04-20-phase-3b-mcp-oauth-design.md).

---

## ADR-0012: simpleeval is the only eval primitive

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Four Phase 4 executors (`transform`, `data-transform`, `if-else`, `while`) evaluate user-supplied expressions over workflow state. Three options:
1. Python `eval()` / `exec()` — full `__builtins__`, module imports, filesystem access. Catastrophic attack surface.
2. Custom parser / AST walk — full control, non-trivial maintenance burden for a secondary feature.
3. `simpleeval` — small library, explicit whitelist of names + functions, blocks `__` attribute access by default, Python expression syntax.

**Decision.** `simpleeval`, exclusively. One wrapper at `src/executors/_eval.py`; every expression-eval codepath routes through it. `eval()` / `exec()` forbidden anywhere in `src/`.

Scope exposes: `variables`, `lastOutput`, `node_results`, and a per-call `extra_names` dict (e.g., `item` for data-transform, `acc` for reduce). No builtins, no imports, no dunder access.

**Alternatives considered.** asteval (extra numpy surface area, not needed). py-mini-racer / JS sandbox (native deps, reintroduces JS-runtime attack surface; behavioural parity with OAB doesn't require syntactic parity).

**Consequences.**
- Users migrating from OAB hit expression syntax differences (no arrow functions, no template literals, no `?.`). `EvalError` includes the failing expression + cause for mechanical translation.
- Adding an operator / function means editing `_eval.py` once. No per-executor drift.
- CI gate: ruff `S307` rule (use of `eval`) runs as error.

**Implemented by.** Phase 4a (commits `10e88ef`..`42fcf92` on `main`, 2026-04-21).

**Related.** ADR-0002 (workflow schema), [Phase 4a spec](../superpowers/specs/2026-04-21-phase-4a-linear-executors-design.md), CLAUDE.md §Conventions ("NEVER `eval()`; use `simpleeval`").

---

## ADR-0013: Conditional edges carry a `branch` label on `WorkflowEdge`

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Phase 4b introduces conditional routing (`if-else`, `while`). Three options for teaching `graph_builder` which outgoing edge represents which branch:
1. Runtime inference from node-data fields (`true_path` / `false_path`). Two sources of truth; UI-drawn edge can silently disagree.
2. Position-based (first edge = true, second = false). Edge-list order is not stable across serialise/deserialise.
3. Explicit `branch` label on each edge. Validation: conditional source requires branch; normal source forbids it.

**Decision.** Option 3. `WorkflowEdge` gains `branch: str | None = None`. Values: `{"true","false"}` for `if-else`, `{"body","exit"}` for `while`. `graph_builder` builds its routing mapping from edge labels at compile time. Legacy data-level fields (`true_path`/`false_path`/`false_path`) become UI-only; the executor + router ignore them.

**Alternatives considered.** (See above.)

**Consequences.**
- Workflow JSON schema gains one optional field. Back-compatible: Phase 1–3 workflows (no `branch`) still validate.
- `_branch_mapping` helper in `graph_builder.py` validates the edge set matches the required branch set exactly (missing/extra/duplicate → WorkflowValidationError).
- Phase 10 UI auto-assigns `branch` when the user drags edges from a conditional node.

**Implemented by.** Phase 4b (commits `62d7196`..`8cbf839` on `main`, 2026-04-21).

**Related.** ADR-0002 (workflow schema), ADR-0012 (simpleeval is the only eval primitive), [Phase 4b spec](../superpowers/specs/2026-04-21-phase-4b-control-flow-design.md).
