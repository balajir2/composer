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
| Master design | [`docs/archive/design-history/2026-04-20-composer-python-port-design.md`](archive/design-history/2026-04-20-composer-python-port-design.md) | Amended when a decision invalidates a section (now archived — superseded by the shipping codebase) |
| Phase design specs | [`docs/archive/phase-history/specs/`](archive/phase-history/specs/) | Written before a phase begins; archived once the phase shipped |
| Decisions log (ADRs) | `docs/decisions.md` (this file) | Append-only; new entry ships in the commit that implements it |
| Changelog | [`CHANGELOG.md`](../CHANGELOG.md) | Updated in the same commit as the change |

**Enforcement rules.**
1. **Doc-with-code:** a commit that changes behavior must include the matching doc update. PRs without matching spec/decision/changelog updates are rejected.
2. **Phase-entry:** before writing code for a phase, its spec file exists and is committed.
3. **Phase-exit:** the "phase N complete" commit updates `CLAUDE.md`'s phase-status table.
4. **ADR anchors:** every ADR has a stable `## ADR-NNNN:` heading so code comments and other specs can link to it.

**Consequences.**
- More commits touching docs. Accepted cost.
- Future maintainers (Balaji in 6 months, IE reviewers, new engineers) can recover intent without reading the full git history.
- Spec drift is structurally prevented — not policed by memory.

**Implemented by.** This file (new); [CHANGELOG.md](../CHANGELOG.md); [CLAUDE.md](../CLAUDE.md) references.

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

**Related.** ADR-0010 (MCP resolver-side instantiation), ADR-0009 (tool provider framework), [Phase 3b spec](archive/phase-history/specs/2026-04-20-phase-3b-mcp-oauth-design.md).

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

**Related.** ADR-0002 (workflow schema), [Phase 4a spec](archive/phase-history/specs/2026-04-21-phase-4a-linear-executors-design.md), CLAUDE.md §Conventions ("NEVER `eval()`; use `simpleeval`").

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

**Related.** ADR-0002 (workflow schema), ADR-0012 (simpleeval is the only eval primitive), [Phase 4b spec](archive/phase-history/specs/2026-04-21-phase-4b-control-flow-design.md).

---

## ADR-0014: Deployment-mode toggle — env var, read at startup

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Composer must run in two deployment shapes: **standalone** (owns user identity, issues JWTs, exposes `/auth/*`) and **embedded** (trusts JWTs from IEP, no identity ownership). Three mechanisms considered:
1. Env var read at startup — immutable within a process.
2. DB-backed `SystemConfig` row — live-switchable.
3. Two separate binaries / packages.

**Decision.** Env var `COMPOSER_DEPLOYMENT_MODE ∈ {"standalone", "embedded"}`, default `"standalone"`, read ONCE at `create_app()` time. Changing requires redeploy.

**Alternatives considered.**
- DB flag: rejected — auth semantics differ fundamentally (which algorithm validates tokens, whether `User` is write-scoped, which routes exist). Live-flipping leaves unauthenticated requests hitting half-migrated state.
- Two binaries: rejected — doubles CI matrix + artifact count for no real isolation gain.

**Consequences.**
- Same codebase serves both modes; mode-branching confined to FastAPI router registration + `get_current_user_id`. No mode-check sprawl.
- Redeploy is the correct boundary for a mode change (auth semantics mismatch is worse than a brief restart).
- Env-var-driven config aligns with IE's Helm / Docker-Compose deployment patterns.

**Implemented by.** Phase 7a (commits `06642f0`..`7de8352` on `main`, 2026-04-21).

**Related.** ADR-0004, ADR-0005, ADR-0015, [Phase 7a spec](archive/phase-history/specs/2026-04-21-phase-7a-deployment-mode-design.md).

---

## ADR-0015: Dev-mode auth fallback — `user_id="dev"` when no Authorization header in `ENVIRONMENT=development`

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Phase 7a replaces every `user_id="dev"` literal with `Depends(get_current_user_id)` that requires a JWT. But 15+ existing integration tests (Phases 3a/3b/4a/4b) don't set Authorization headers. Two options:
1. Rewrite every integration test to obtain and send a JWT.
2. Keep a fallback: when `environment=development` AND no header, return `user_id="dev"`.

**Decision.** Option 2. `get_current_user_id` returns `"dev"` if AND only if:
- Authorization header absent or empty
- `settings.environment == "development"`

Production deployments set `ENVIRONMENT=production` (unset or typo → not `"development"`). Fallback is unreachable in production.

**Alternatives considered.** Config flag `enable_dev_auth_fallback` (rejected — doubled state surface); test-only fixture injecting auth (considered for Phase 7b, but 7a's minimal churn is preferred).

**Consequences.**
- Startup logs `"auth: dev-mode fallback ENABLED"` as a prominent WARN line when environment=development. Prod-like deploys without `ENVIRONMENT=production` set see the log and fix the env var.
- Existing integration tests require zero changes.
- Phase 7a's auth integration tests set real Authorization headers explicitly; they bypass the fallback.

**Implemented by.** Phase 7a (commits `06642f0`..`7de8352` on `main`, 2026-04-21).

**Related.** ADR-0014, ADR-0005.

---

## ADR-0016: User-approval uses LangGraph `interrupt()` + background-task resume

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** User-approval nodes pause workflow execution until a human decides approved/rejected. Three mechanisms considered:
1. Blocking request — `POST /executions` holds the HTTP connection open until approval arrives. Pins an API worker indefinitely.
2. Poll + re-run from scratch on resume. Wasteful + incorrect for workflows with side effects (HTTP, LLM, MCP calls re-execute).
3. Checkpoint + resume. LangGraph's `interrupt()` persists state via our existing `PrismaCheckpointSaver`; resume loads the checkpoint + continues from the paused node.

**Decision.** Option 3. `UserApprovalExecutor` calls `interrupt({node_id, prompt})`. After `compiled.ainvoke(...)` returns, `LangGraphExecutor.run` calls `compiled.aget_state(config)` and checks `snapshot.next` to detect a pause — in recent LangGraph versions the Pregel runtime catches `GraphInterrupt` internally and returns cleanly rather than propagating the exception (caught by real-Neon integration testing, fix in commit `b07d8de`). On pause, the executor marks execution `waiting_approval` + merges `_pending_approval_node`/`_pending_approval_prompt` into `variables`. `POST /executions/{id}/resume` writes an `Approval` row + schedules a `BackgroundTask` that calls `compiled.ainvoke(Command(resume=decision), config={"thread_id": ...})`. LangGraph loads the checkpoint + continues.

**Alternatives considered.** See above. Option 1 rejected on operational grounds; Option 2 rejected for correctness + cost.

**Consequences.**
- Phase 1's `PrismaCheckpointSaver` is already the right abstraction — no checkpointer changes.
- Background-task model → clients poll for completion. Phase 5b adds SSE; doesn't change the model.
- `Approval` table is the system of record for decisions; future audit queries are simple DB reads.
- A resume on a completed execution → 409 (status check).
- A resume while a prior resume task is in flight → 409 (same check).
- Workflows with user-approval nodes use Phase 4b's conditional-edges machinery with branches `{"approved", "rejected"}`.

**Implemented by.** Phase 5a (commits `74fe159`…`b07d8de`, 2026-04-21).

**Related.** ADR-0001 (PrismaCheckpointSaver), ADR-0013 (WorkflowEdge.branch — user-approval is a conditional source), ADR-0015 (dev-mode auth; any authenticated user can resume in 5a), [Phase 5a spec](archive/phase-history/specs/2026-04-21-phase-5a-user-approval-design.md).

---

## ADR-0017: SSE streaming uses in-process asyncio event bus with snapshot-on-subscribe

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Clients currently poll `GET /executions/{id}` for progress. Three approaches for real-time:
1. In-process asyncio bus (this ADR).
2. Postgres `LISTEN/NOTIFY` — works across workers; adds a dedicated DB connection per subscriber.
3. Redis pub/sub — new infra dependency.

**Decision.** In-process asyncio bus. Matches the current single-worker target; YAGNI for multi-worker until we actually scale. SSE (not WebSocket) because SSE is stateless, plays well with standard HTTP middleware, and needs no new dependencies. Phase 9 adds WebSocket as a separate channel.

**Alternatives considered.** `LISTEN/NOTIFY` rejected on cost: one DB connection per subscriber would strain Neon. Redis rejected on ops cost: new infra just for event fanout.

**Consequences.**
- Zero new infrastructure.
- Multi-worker limitation: subscribers only receive events from the worker that's running the executor. Documented + acceptable until we scale past one worker.
- Event loss under backpressure: bounded per-subscriber queue (size 128) drops oldest on overflow. DB remains authoritative; stream is advisory.
- No persistent event log: a client that subscribes late only sees the current-status snapshot, not the past node trail.
- Five event types in MVP: `status-change`, `node-start`, `node-complete`, `approval-pending`, `approval-resumed`. LLM token streaming is Phase 10+.
- Keepalive via SSE comment every 15s — detects dead clients and prevents proxy idle-closes.

**Implemented by.** Phase 5b (commits `40ef435`…`a6a4a98`, 2026-04-21).

**Related.** ADR-0001 (PrismaCheckpointSaver — same single-process assumption), ADR-0016 (Phase 5a emits `approval-pending` / `approval-resumed`), [Phase 5b spec](archive/phase-history/specs/2026-04-21-phase-5b-sse-streaming-design.md).

---

## ADR-0018: Guardrails implemented as LLM-based classifier

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** OAB's guardrails executor shipped a 4-word hardcoded bad-word list with an explicit `TODO: Integrate with content moderation APIs`. Composer rebuilds the feature per OAB's data-shape contract (`piiEnabled`, `moderationEnabled`, `jailbreakEnabled`, `hallucinationEnabled`, `actionOnViolation`). Three implementation options:
1. Faithful stub port (copy the hardcoded word list). Matches OAB exactly but is useless in prod.
2. LLM-based classifier using the Phase 2 `build_chat_model` framework. One LLM call per enabled check; concurrent via `asyncio.gather`.
3. Direct integration with a purpose-built moderation API (e.g., OpenAI Moderation). Most accurate for moderation specifically, but adds a new API dependency and doesn't cover PII/jailbreak/hallucination.

**Decision.** Option 2. Prompts are frozen in source (not user-configurable) — guardrails is a safety feature whose behavior should be deterministic and auditable; users who want custom rules compose `agent + if-else`.

**Alternatives considered.** Option 1 rejected on usefulness. Option 3 rejected on dependency + coverage (only covers one of four check types). A future phase may add a provider framework (like LLM providers) if multiple guardrails backends are demanded.

**Consequences.**
- Works across all four Phase 2 LLM providers without additional deps.
- Latency scales with enabled-check count; `asyncio.gather` parallelizes.
- Cost per execution is 1 LLM call per enabled check — documented in CHANGELOG so users can pick cheap models (Haiku) when guardrails run hot.
- Accuracy depends on the chosen model. Fast/cheap models (e.g., Haiku) may have more false negatives on subtle attacks. Users can tune.
- Prompt changes are code changes — gives us an audit trail via git history.
- Response parsing is conservative: ambiguous answers are treated as `NO` (pass). This is a deliberate anti-false-positive bias; guardrails is defense-in-depth.
- Output dual-written: structured dict on `_guardrails_result` (for `if-else` branching) + human-readable summary on `lastOutput` (for downstream display).
- `action_on_violation='block'` raises `GuardrailViolationError` → execution fails.

**Implemented by.** Phase 6b (commits `c4904ed`…`9121111`, 2026-04-21).

**Related.** ADR-0006 (LLM provider framework — Phase 2), ADR-0012 (simpleeval for if-else conditions — how downstream branches on `_guardrails_result.passed`), [Phase 6b spec](archive/phase-history/specs/2026-04-21-phase-6b-guardrails-design.md).

---

## ADR-0019: `/executions/{id}/resume` handles both user-approval and arcade-auth flows

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Phase 6d ships the `arcade` executor — HTTP integration with arcade.dev's tool-execution API. Arcade's auth flow requires the user to visit an OAuth URL externally and return to the workflow. That's structurally identical to Phase 5a's user-approval pattern (pause via `interrupt()`, resume via `POST /executions/{id}/resume`). Two options:
1. **Reuse `/resume`** — the same endpoint handles both user-approval decisions AND arcade-auth retries. The executor owns the re-entry semantic.
2. **Dedicated `/arcade-auth-check` endpoint** — separate surface area for each interrupt source.

**Decision.** Option 1 — reuse `/resume`. `ArcadeExecutor` reads `_approval_<node_id>` from state on re-entry: `approved` means "retry auth", `rejected` means cancel (raise `ArcadeUserCanceledError`). The SSE `approval-pending` event payload carries optional `auth_url` / `auth_id` / `tool_name` fields for Arcade-style interrupts; existing user-approval consumers ignore unknown keys.

**Alternatives considered.** Option 2 was rejected on YAGNI: the interrupt/resume shape is identical across both cases, only the executor's re-entry logic differs. Adding a second endpoint duplicates auth/validation/BackgroundTask scheduling for no gain.

**Consequences.**
- Single endpoint, single event type, single bus primitive — less surface area.
- Future auth-interrupt executors (Phase 7+ Slack/Gmail/etc. tool integrations) reuse the same pattern without new infrastructure.
- `_approval_<node_id>` in state becomes a generic "resume decision" signal, not specifically a user-approval artifact. Documented.
- Retry counter bounded at `MAX_RETRIES=3` in `ArcadeExecutor` prevents infinite loops on broken OAuth URLs; stored in `state.variables["_arcade_retries_<node_id>"]`.
- Payload extensibility: `approval-pending.payload` gains optional fields — existing SSE consumers are unaffected (dict, unknown keys ignored).

**Implemented by.** Phase 6d (commits `f847147`…`0f262c7`, 2026-04-21).

**Related.** ADR-0016 (user-approval + interrupt/resume — the primitive being reused), ADR-0017 (SSE streaming — the `approval-pending` event type extended here), [Phase 6d spec](archive/phase-history/specs/2026-04-21-phase-6d-arcade-design.md).

---

## ADR-0020: VectorDB provider framework — one file per provider

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** Phase 6e ships the `vector-db` executor supporting 5 providers (Pinecone, Qdrant, Chroma, Weaviate, Milvus) with OpenAI embeddings. OAB's implementation is 613 LOC in a single file with an if-elif dispatch. Composer's structure options:
1. Monolithic file (match OAB) — quick to port, hard to test providers in isolation.
2. Abstract base class / Protocol with provider subclasses — idiomatic OO but overkill for stateless HTTP calls.
3. One file per provider exporting a top-level `async def query(embedding, config) -> list[VectorDbResult]`. Executor dispatches via a dict map.

**Decision.** Option 3. Same conceptual pattern as Phase 2's LLM provider framework (ADR-0006) but even lighter — no class hierarchy, just functions. `src/vectordb/providers/{pinecone,qdrant,chroma,weaviate,milvus}.py` each export `query()`. `src/vectordb/providers/base.py` defines the shared `VectorDbResult` + `QueryConfig` dataclasses (frozen, typed). Executor holds `_PROVIDERS: dict[str, _ProviderFn]` and looks up by `node.data.provider`.

**Alternatives considered.**
- **Monolithic (Option 1):** rejected on testability. Each provider deserves its own `pytest-httpx` test file.
- **ABC/Protocol (Option 2):** rejected on YAGNI. Providers are stateless; no polymorphic object state to justify the ceremony.
- **External plugin registry:** rejected on YAGNI. Providers are known + finite + internal; no need for dynamic registration.

**Consequences.**
- Each provider independently testable in isolation.
- Adding a new provider = one new file + one new dict entry in the executor.
- `QueryConfig` + `VectorDbResult` dataclasses are the interface contract; change them and all providers must adapt (compile-time check via type annotations).
- Embedding providers (OpenAI only in 6e) follow the same shape — `src/vectordb/embedding.py` ships `embed_text_openai()`; non-OpenAI providers raise `NotImplementedError` until a later phase.
- No shared HTTP client pool across providers (each opens/closes its own `httpx.AsyncClient` for one call). Acceptable — vector-db nodes are low-frequency relative to agent/mcp nodes.

**Implemented by.** Phase 6e (commits `e2068d2`…`84cd4a5`, 2026-04-21).

**Related.** ADR-0006 (LLM provider framework — analogous pattern), [Phase 6e spec](archive/phase-history/specs/2026-04-21-phase-6e-vector-db-design.md).

---

## ADR-0021: Composer's Phase 8 security policy

**Status.** Accepted.
**Date.** 2026-04-21.

**Context.** At end of Phase 7b, Composer has functional auth (Phase 7a) but loose read-authz (anyone authenticated can `GET /workflows/{id}` or `/executions/{id}` regardless of owner), no rate limits, and no size caps. This is fine for dev but not production-shippable. Phase 8 closes these gaps before Phase 9 cutover / Phase 10 UI fork.

**Decision.**
- **Authz policy.** Private workflows + all executions are owner-only for read. Non-owner reads of private resources return **404** (not 403) to avoid a tight info leak that reveals existence. Public workflows (`is_public=True`) are world-readable for any authenticated user. MCP servers remain owner-only as-shipped.
- **Size caps.** Workflows capped at `max_workflow_nodes=100`, `max_workflow_edges=200`. Execution inputs capped at `max_execution_input_bytes=1_000_000` (1 MB, JSON-serialized). All three settings are tunable via env. Enforcement at write time (create/update/POST /executions), not at execution time — rejecting late wastes LLM calls.
- **Rate limiting.** In-memory token-bucket per-key (user_id for authenticated, IP for anonymous). Defaults: 30/min `/executions`, 10/min `/auth/login`, 5/min `/auth/register`, 30/min `/auth/refresh`, 60/min `/resume`, 10/min `/mcp-servers/{id}/test-connection`. 429 response with `Retry-After: N` header and JSON `{detail}`. Not rate-limited: list/search GETs, workflow CRUD, SSE, `/health`, `/auth/me`.
- **Security regression tests.** 13 unit tests + 1 real-Neon integration test pin the invariants above.

**Alternatives considered.**
- **Redis-based rate limiter:** rejected for Phase 8 as overkill for single-worker target. Phase 9 introduces Redis when we scale horizontally; a thin `RateLimiter` class makes the swap mechanical.
- **403 on private-read-by-non-owner:** rejected in favor of **404** — 403 reveals resource existence, 404 doesn't. Both are strictly compliant with REST; 404 is tighter in the security sense.
- **No size caps:** rejected because a user could DoS the worker with a 10,000-node workflow.
- **Rate-limit via storage (Postgres):** rejected for latency (one round-trip per request).

**Consequences.**
- **Breaking change vs Phase 7b:** existing integration tests assumed world-read access. Phase 8 Task 1 tightens that to owner-only-for-private + public-for-world. Pre-existing tests that use dev-mode `user_id="dev"` continue to work (owner = dev). Tests that simulate "user A reads user B's workflow" will start returning 404 (behavioral change).
- **In-memory rate-limit state** is lost on worker restart. Acceptable for single-worker deployment. Phase 9 cutover replaces with Redis-backed storage as part of the multi-worker scale-out.
- **Rate-limit header leak:** the `Retry-After` header technically reveals rate-limit state. Standard REST behavior; accepted.
- **Size caps are defense-in-depth.** Real OAB workflows are 10-30 nodes; 100 is comfortable. Users hitting the cap can tune via env or split workflows.
- **Info-leak via timing.** Tight-read-authz returns 404 for both "doesn't exist" and "not owner" — but the timing might differ (owner path reads from DB, non-owner path reads + checks + 404). Phase 9+ could constant-time this if demanded; not in scope for Phase 8.
- **No SSRF protection on `http` executor.** Private-network targets (169.254.169.254, 10.0.0.0/8) are reachable. Phase 9+ adds allowlist/blocklist.

**Implemented by.** Phase 8 (commits `222749e`…`20c4309` on `main`, 2026-04-22).

**Related.** ADR-0014 (deployment mode — shapes the auth model this builds on), ADR-0015 (dev-mode fallback), ADR-0016 (user-approval), [Phase 8 spec](archive/phase-history/specs/2026-04-21-phase-8-security-hardening-design.md).

---

## ADR-0022: Composer's Phase 9 cutover policy

**Status.** Accepted 2026-04-22.

**Context.** Phase 9 moves Composer from feature-complete to production-deployable. The cutover touches data migration (OAB Convex → Composer Postgres), deploy-time secrets management (LLM keys), an auth role expansion (admin), and a real-time protocol change (SSE → WebSocket per IE DES-007). Each decision below was made deliberately during Phase 9 brainstorming; together they define what "cutover readiness" means.

**Decision.**

1. **No user migration.** OAB's `users` table is not copied. Clerk IDs are dropped. Email is the stable cross-system identity.
2. **Email-based reconciliation.** Migrated rows carry `original_owner_email` (nullable, lower-cased); `user_id = NULL` until a Composer User with matching email exists, after which `composer reconcile --email X` or `PATCH /workflows/{id}/owner` claims the rows. `original_owner_email` stays indefinitely as an audit column. `McpServer.user_id` was made nullable in a follow-on migration so all three migratable tables share this "orphaned pending reconciliation" shape.
3. **Admin read/publish bypass; no admin DELETE bypass.** Admins bypass the Phase 8 owner/public check on reads (workflows/executions/events) and on `PUT /workflows/{id}` (including flipping `isPublic`). DELETE remains strict owner-only to avoid silent destructive bypass.
4. **LLM keys in Postgres; Vercel sync at deploy time.** Bounteous owns the source of truth. Runtime reads env vars (unchanged); `composer keys sync --target vercel` pushes decrypted values to Vercel env via Vercel API. Same AES-256-GCM encryption mechanism as MCP OAuth tokens.
5. **WebSocket replaces SSE atomically.** Event bus switches to DES-007 event shapes in the same commit that deletes the SSE endpoint. No transitional window; no "legacy SSE" endpoint kept.
6. **DES-007 event contract from blueprint.** Phase 9 uses the event shape documented in Composer's own blueprint `docs/design/2026-04-15-composer-03-engineering-blueprint.md` §7.3. Verification against IE source is a Phase 10 pre-work checkpoint — any drift gets fixed as a bugfix commit then.
7. **Skip approvals + LangGraph checkpoints migration.** Approvals are audit-only data; JS checkpoints are Python-incompatible. In-flight OAB executions (status=running/waiting_approval) are rewritten to `status=failed` with an explanatory error and are not recoverable on Composer.

**Consequences.**
- **Simpler migration path:** no password migration, no forced reset flow, no Clerk-ID-to-cuid mapping table.
- **Post-cutover manual step:** ops runs `composer reconcile --email X` once per user (automatable via post-login hook in Phase 10 SSO).
- **Admins exist but have no self-serve promote endpoint** — requires DB write.
- **One-time data loss:** OAB executions in flight at cutover become `failed` records with an explanatory error. Acceptable given the internal user base.
- **Vercel lock-in for LLM keys is mitigated:** keys live in Bounteous-owned Postgres; Vercel can be replaced without losing the keys.
- **WebSocket-only streaming** means dev tooling can't use `curl` for live events anymore. Alternative: small Python WS client for ad-hoc debugging (`starlette.testclient.TestClient.websocket_connect` or the `websockets` package).
- **`McpServer.user_id` nullable** is a schema widening — existing rows unaffected; enables uniform orphan semantics across the three migratable tables.

**Implemented by.** Phase 9 (commits `fc059e1`…`5b9bf5f` on `main`, 2026-04-22).

**Related.** ADR-0014 (deployment mode), ADR-0015 (dev-mode auth fallback), ADR-0021 (Phase 8 security policy — Phase 9 admin bypass amends this), [Phase 9 spec](archive/phase-history/specs/2026-04-22-phase-9-cutover-readiness-design.md).

---

## ADR-0023: Composer frontend policy + three-audience enterprise UX

**Status.** Accepted 2026-04-23.

**Context.** Phase 10 shipped Composer as a usable enterprise product. The original 2026-04-20 design doc called for forking OAB's Next.js frontend; during Phase 10 brainstorming this was replaced with a fresh enterprise UX build. Three audiences (Designer / End User / Admin), external-invoke API endpoints, and a unified tools-and-MCPs catalog were added.

**Decision.**

1. **Fresh UX, not OAB visual port.** OAB is behavioral reference; frontend built fresh in Next.js 14 App Router + Tailwind + shadcn/ui.
2. **Monorepo under `composer/frontend/`.** Not a separate repo. OpenAPI TS types generated in-place from Composer's FastAPI schema.
3. **Single Next.js app, role-aware routes.** `/designer/*`, `/runs/*`, `/admin/*` guarded at layout level. One build, one deploy.
4. **Tailwind + shadcn/ui over Ant/Mantine.** Installed `base-nova` style (on `@base-ui/react`, not Radix — the default shipped by `shadcn init` as of v4); CSS variables hook for future Bounteous branding. `Button asChild` is unsupported in this style; use `<Link>` + `buttonVariants()`.
5. **Three audiences, not two.** Admin added as first-class with dedicated UI (user management, catalog publishing, LLM keys, workflow override).
6. **Unified Tools palette for designers.** Built-in providers + shared MCPs merge into one palette; architectural distinction hidden from designers (visible only in Admin UI).
7. **Email as Azure SSO identity link.** Phase 9's email-as-cross-system-identity extended to Azure AD via `/auth/sso-exchange`. Standalone `/auth/login` preserved. `User.passwordHash` nullable.
8. **Role-based workflow access, no groups.** `isPublic=true` → any authenticated user + any API key; private → owner + admin.
9. **Production-workflow state + externalSlug + ApiKey.** OAB's "production" ported as `Workflow.isProduction` + globally-unique `externalSlug`. External invoke via `POST /api/run/{slug}` with `Authorization: Bearer ck_<key>`.
10. **Async default, sync opt-in.** External invoke returns 200 + async shape by default; `sync=true` waits up to `timeoutSeconds` (max 300).
11. **No workflow versioning.** Edits to production workflows propagate immediately. Versioning deferred.
12. **SSO exchange via backend endpoint.** NextAuth validates Azure token client-side, then calls `POST /auth/sso-exchange` — Composer stays the JWT authority.
13. **New admin-surgical PATCH endpoints** (`PATCH /mcp-servers/{id}/shared`, `POST /admin/users/{id}/role`) supplant the Phase 9 SQL-only promotion path and avoid the full-body PUT contract.
14. **Vercel for frontend, container for backend.** Long-lived WebSocket (DES-007) doesn't work on Vercel Serverless Functions; backend recommended on Fly/Render/App Runner. Frontend stays on Vercel.

**Consequences.**
- **Simpler deployment.** Single Next.js app + single backend; Vercel for frontend; separate container for backend.
- **Fresh UX cost ≫ port cost.** Offset by not carrying OAB's design debt.
- **Admin UI surface small but real.** Tool catalog + LLM keys + workflow override + user management cover the common ops scenarios; advanced flows (audit trails, groups) deferred.
- **API keys as new auth path.** Coexists with JWT; distinguished at the `Authorization: Bearer` prefix (`ck_` → API key; otherwise JWT).
- **shadcn base-nova divergence.** Documented in Phase 10 Task 6 concerns; downstream subagents adapted (e.g., `<Link>` + `buttonVariants()` instead of `<Button asChild>`).
- **NextAuth v5 still in beta.** Pinned to `5.0.0-beta.31`; upgrade to stable when released.
- **WebSocket deployment constraint** forces a container-based backend, documented in `docs/operations/vercel-setup.md`. In practice the backend runs on GCP Cloud Run alongside the frontend (see `docs/operations/gcp-cloud-run-setup.md`) rather than the mixed Vercel-frontend/separate-container split this ADR anticipated — `vercel-setup.md` remains as an alternate path.

**Implemented by.** Phase 10 (commits `a6cd130`…`4f3a4d0` on `main`, 2026-04-23).

**Related.** ADR-0022 (Phase 9 cutover — email identity extended here), ADR-0021 (Phase 8 security — admin-bypass policy reused), ADR-0014 (deployment modes), [Phase 10 spec](archive/phase-history/specs/2026-04-22-phase-10-composer-frontend-design.md).

---

## ADR-0024: Password reset — admin-only, third JWT type gates change-password, mustChangePassword cuts off live sessions

**Status.** Accepted 2026-07-09.

**Context.** Composer had no way for a user to recover a forgotten password, nor for an admin to reset one for them. The user explicitly chose an admin-only reset (no email dependency) over a self-service emailed-link flow. Implementing the "must change password" gate needed a way to issue a session that can do exactly one thing (call `/auth/change-password`) without granting normal API access — and, once built, review surfaced that the gate had to cut off *existing* sessions too, not just new logins.

**Decision.** Add a third JWT `type` discriminator, `password_change`, alongside the existing `access`/`refresh` types in `src/security/jwt.py`. Every existing route dependency (`get_current_user_id`) already rejects any token whose `type` isn't `"access"`, so a `password_change` token is automatically unusable everywhere except one new dependency, `get_user_id_allow_password_change` (`src/security/auth.py`), that explicitly accepts either an access token or a password_change token — used solely by the new `POST /auth/change-password` endpoint.

`POST /auth/login` returns this restricted token instead of a normal pair when `User.mustChangePassword` is true. `POST /admin/users/{id}/reset-password` (admin-only) generates a random temp password via `secrets.token_urlsafe(12)`, hashes it, and sets that flag. `POST /auth/change-password` always re-verifies the caller's current password before accepting a new one, regardless of which token type authorized the call.

**A follow-up fix, made during code review, closes a session-lifetime gap the initial implementation left open:** `mustChangePassword` was only checked at login, so an already-logged-in user (access token, 8h TTL; refresh token, 30d TTL) could keep using — and refreshing — their session indefinitely without ever hitting the gate. Fixed by adding `_ensure_active_and_no_pending_password_change` (checks both `isActive` and `mustChangePassword`) and wiring it into `get_current_user_id` (used by virtually every authenticated route) and into `POST /auth/refresh`. The original `_ensure_active_user` (isActive-only) is deliberately left untouched and still used by `get_user_id_allow_password_change`, since a user completing a forced reset legitimately has `mustChangePassword=True` and must not be locked out of the one endpoint that lets them clear it.

On the frontend, NextAuth's `authorize()`/`jwt()`/`session()` callbacks thread `mustChangePassword`/`passwordChangeToken` through a "restricted" session that never carries a usable Composer access token. A second follow-up closed a route-level gap: `middleware.ts` and `requireSession()` (`lib/composer-session.ts`) now redirect any `mustChangePassword` session to `/change-password`, and a third fix added `requireAnySession()` (session-exists-only, no mustChangePassword branch) so the `/change-password` route itself doesn't create a redirect loop while still blocking completely anonymous visitors.

**Consequences.**
- No new route-level authorization logic needed anywhere else for the *type*-discrimination gate — it's automatic. The session-lifetime and route-level gaps were real oversights caught only by review, not by the original design — a reminder that "add a flag" features need an explicit sweep of every place a session is trusted (login, refresh, every route dependency, frontend middleware, and every page-level guard) before they're actually complete.
- Admins never see or set a user's real password (only a randomly generated temp one).
- Email-based self-service reset remains out of scope; if added later, it can reuse the same `password_change` token type.

**Implemented by.** `docs/archive/phase-history/plans/2026-07-09-account-workflow-sharing-plan.md`, Part A (commits from `6793c64` through the Part A frontend commits on `main`, 2026-07-09).

**Related.** ADR-0005 (Phase 1 API surface + authentication), ADR-0015 (dev-mode auth fallback).

---

## ADR-0025: Workflow assignment — additive join table, all-or-nothing access

**Status.** Accepted 2026-07-10.

**Context.** A workflow could only ever have one owner (`Workflow.userId`). Investigation of OAB's actual schema (`convex/schema.ts`) found OAB never supported many-to-many assignment either — it's a genuine enhancement over OAB's original design, not a restoration.

**Decision.** Add `WorkflowAssignment` (`workflowId`, `userId`, `assignedById`, `assignedAt`; unique on `(workflowId, userId)`) as a layer on top of the existing single-owner field, which keeps its meaning unchanged ("who created this / who transfers/deletes it"). Assignment grants full read+write access (open, edit, run) — there is no view-vs-edit split. Only the owner or an admin can grant/revoke assignments; delete and owner-transfer remain owner/admin-only, untouched by assignment. A new `GET /users/search` endpoint (any authenticated active user, results limited to id/email/displayName, rate-limited at 30/min/user) lets non-admin owners find people to share with, since the existing `/admin/users` listing is admin-only.

`GET /workflows/{id}` and `PUT /workflows/{id}` authorization both gained an assignee check (`_has_assignment`) alongside the existing owner/admin/isPublic checks. `DELETE` and `/owner` transfer were deliberately left alone. Both `list_workflows` (`mine=true` and the default non-admin view) and `search_workflows` needed a third `OR` clause (`assignments.some.userId`) so assigned-not-owned workflows are actually visible and findable anywhere in the UI — the search-endpoint gap was caught only after the list-endpoint fix shipped, underscoring that "make it visible" has more than one call site to update.

**Follow-up fixes made during code review:**
- `grant_workflow_assignment`/`revoke_workflow_assignment` initially let Prisma exceptions escape as uncaught 500s on double-grant/double-revoke. The grant side needed a straightforward `UniqueViolationError` → 409 catch. The revoke side needed a genuinely different fix: Prisma Python's generated `delete()` already catches `RecordNotFoundError` internally and returns `None` rather than raising — so the naive `except RecordNotFoundError` was dead code that could never fire against the real client. The correct fix checks `delete()`'s return value for `None`.
- The frontend `ManageAssigneesDialog` shared one mutation's `isPending` flag across every row, so a still-visible row's button re-enabled before the invalidated query's refetch actually removed it, letting a rapid second click hit the backend's 409/404 and surface a confusing error. Fixed with per-row pending-id tracking instead of one shared flag.
- The owner-settings page rendered the "Manage access" section unconditionally for anyone who could load the page — including non-owner assignees, who have full read/write access to the workflow itself but are correctly 403'd by the assignment-management endpoints (owner/admin-only). Fixed by gating the section on `role === "admin" || workflow.userId === currentUserId`.

**Consequences.**
- Per-assignee permission levels (view-only, etc.) are explicitly out of scope; if needed later, it's a new column on `WorkflowAssignment`, not a schema rework.
- "A user with full API access to a resource" and "a user allowed to manage who else has that access" are different authorization questions — this feature has two now, and every new UI surface touching either one needs to ask the right question, not just check "can I see this page."

**Implemented by.** `docs/archive/phase-history/plans/2026-07-09-account-workflow-sharing-plan.md`, Part B (commits from `c8df7e1` through `6e14494` on `main`, 2026-07-09/10).

**Related.** ADR-0021 (Phase 8 security policy — owner-only 404 pattern), ADR-0024.

---

## ADR-0026: Autosave replaces "transfer loses flow data" root cause

**Status.** Accepted 2026-07-10.

**Context.** A reported bug — "reassigning a workflow's owner leaves only Start+End nodes" — turned out, on investigation, to have no matching code path: the reassign endpoint only ever updates `Workflow.userId`. The Designer had no autosave at all; nodes/edges only reached Postgres via an explicit Save click. The real failure mode is that an owner can edit a flow, never click Save, and the database row genuinely only ever holds the Start+End scaffold from creation — which becomes visible the moment a different person (a new assignee, per ADR-0025) opens it fresh.

**Decision.** Add `useAutosave` (`frontend/lib/use-autosave.ts`): a debounced (3s) autosave that reuses the existing manual-save mutation and `PUT /workflows/{id}` path — no new backend endpoint. A `saveNow()` variant backs the manual Save button so both paths share one status state machine (`idle → dirty → saving → saved/error`), surfaced next to the Save button, plus a `beforeunload` guard that blocks tab-close while unsaved. No backend or transfer-endpoint change was needed or made.

**Follow-up fixes made during code review — all in `frontend/app/designer/[workflowId]/page.tsx`, none in the hook itself:**
- The debounced auto-trigger's rethrow (needed so `saveNow()` can propagate errors to its caller) produced an unhandled-promise-rejection warning when fired from `markDirty()`'s fire-and-forget path. Fixed by swallowing the rethrow only on that path (`runSave().catch(() => {})`), while `saveNow()` still awaits and propagates.
- `WorkflowCanvas`'s existing `onNodesChange`/`onEdgesChange` effects fire on initial mount with the just-loaded data (no cleanup function), so naively wiring `markDirty()` into them flagged every workflow open as "unsaved changes" — a false status, a false `beforeunload` warning, and a spurious autosave PUT on every view, not an edge case. A first attempt gated this with "have I been invoked before" booleans; that broke under React 18 Strict Mode (Next.js's dev-mode default), which double-invokes no-cleanup mount effects and made the false-dirty bug reappear in `npm run dev` specifically. The working fix compares the *content* of each callback's incoming nodes/edges (by id: type/position/data for nodes, source/target/sourceHandle/label for edges) against a baseline captured once per mount, rather than counting invocations — immune to both Strict Mode's double-invoke and ReactFlow's own post-mount dimension-sync producing new array instances with unchanged content.
- Next.js's App Router doesn't guarantee a remount when a dynamic route's param changes via client-side navigation between two instances of the same route (e.g. `/designer/A` → `/designer/B`) — without forcing one, stale refs (and the mount-vs-edit baseline above) would leak from the previous workflow into the next. Fixed with `key={params.workflowId}` on the page's inner component, forcing a genuine unmount/remount on every workflow switch.

**Consequences.**
- Last-write-wins remains the concurrency model; no multi-editor conflict resolution was introduced (explicit non-goal, consistent with the current single-editor assumption).
- All three follow-up fixes were caught by code review, not the original design or the initial test suite — mount-effect timing, Strict Mode's dev-only double-invoke semantics, and App Router's remount-on-param-change behavior are exactly the kind of framework-timing subtlety that's invisible until something exercises it (here, a reviewer reasoning through *why* an effect fires, not just *that* it fires).
- If a genuine transfer-triggered data-loss bug surfaces later (i.e., reproduced with confirmed pre-save content), it is a different, new investigation — this ADR only closes the no-autosave gap that explained the reported symptom.

**Implemented by.** `docs/archive/phase-history/plans/2026-07-09-account-workflow-sharing-plan.md`, Part C (commits `a901e18` through `ac922a7` on `main`, 2026-07-10).

**Related.** ADR-0025.

---

## ADR-0027: Self-service password reset — additive to ADR-0024, same token mechanism

**Status.** Accepted 2026-07-10.

**Context.** ADR-0024 deliberately shipped admin-only password reset (no email dependency), a considered trade-off at the time. The user later asked for a standard self-service "Forgot password?" flow, explicitly wanting it reused from existing infrastructure rather than built as new auth machinery.

**Decision.** Add two endpoints that reuse the existing `password_change` JWT type/mechanism from ADR-0024, rather than inventing a second token concept: `POST /auth/forgot-password` (looks up the email, and — only for a real, password-based account — emails a link containing a fresh `password_change` token; always returns 204 regardless of whether the email matched anything, closing the account-enumeration vector) and `POST /auth/reset-password` (verifies the token directly via `verify_password_change_token`, sets the new password — critically, without asking for the current password, since the whole point is the user forgot it). Email delivery reuses the existing `ResendEmailProvider` already wired up for the workflow email-node executor — no new email infrastructure. The shared `jwt_password_change_ttl_seconds` was bumped from 10 to 30 minutes to serve both the admin-handoff case (interactive) and the new email case (user needs time to check their inbox).

**Consequences.**
- `/auth/reset-password` intentionally does NOT use the `get_user_id_allow_password_change` dependency (which also accepts normal access tokens) — it calls `verify_password_change_token` directly, since this route must be reachable by a fully anonymous caller and must reject access/refresh tokens outright.
- Resend's free-tier verified sending domain (`script-research.online`) is unrelated to and independent from the app's own domain (`flowcomposer.online`) — this is normal and doesn't need to change.
- Email-delivery failures inside `/auth/forgot-password` are deliberately swallowed (logged, not surfaced) so a Resend outage can't be used to distinguish "this email exists" from "this email doesn't," matching the endpoint's always-204 contract.

**Implemented by.** `docs/archive/phase-history/plans/2026-07-10-self-service-password-reset-plan.md` (commits on `main`, 2026-07-10).

**Related.** ADR-0024 (admin-only password reset — the mechanism this reuses).

## ADR-0028: Jira node — per-node credentials, encrypted at rest, redacted on every read

**Status.** Accepted 2026-07-10.

**Context.** The `jira` node type was built to let a designer configure Jira Cloud credentials (`domain`, `email`, `apiToken`) directly on the node, so a workflow that talks to Jira needs no shared MCP registration or admin-managed key — the trade-off being that the credential now lives inline in the workflow's `nodes` JSON rather than in a dedicated table like `LlmApiKey` (ADR unspecified but see `src/api/admin_llm_keys.py`) or the MCP OAuth-token store (the six MCP fixes, CLAUDE.md §1). A security review of the initial implementation found the `apiToken` was stored and returned in plaintext: every `GET /workflows`, `/workflows/search`, and `/workflows/{id}` response included it verbatim to anyone who could read the workflow (owner, assignee, admin, or the public if `isPublic`). The same review also found `JiraNodeData.api_token` had no `alias="apiToken"`, so the frontend's camelCase payload silently failed to populate the field at all — every UI-created Jira node was actually running with no credentials.

**Decision.** Keep the per-node credential model (it's the point of the feature) but close the storage/exposure gap:
- Fix the alias bug: `api_token: str | None = Field(default=None, alias="apiToken")`.
- Encrypt `apiToken` with the existing AES-256-GCM helpers (`src/security/encryption.py`) the moment a workflow is created or updated, prefixed `enc:v1:` so encrypted values are self-identifying (`is_jira_api_token_encrypted`) without a schema flag.
- Redact `apiToken` to a fixed `••••••••` marker on every read path (`get_workflow`, `list_workflows`, `search_workflows`, and the mutation endpoints' own responses) — the ciphertext itself never leaves the server, not just the plaintext.
- On update, if the incoming `apiToken` is exactly the redacted marker (the designer didn't touch the field), preserve whatever was already stored for that node id instead of encrypting the literal marker string — otherwise every no-op save would brick the credential.
- Decrypt only inside `JiraExecutor.arun`, in-memory, immediately before handing the value to `JiraProvider` for the live API call. `decrypt_jira_api_token` passes non-prefixed values through unchanged, so tokens saved before this fix shipped keep working with no backfill migration required.
- Thread `langsmith_config=get_current_langsmith()` through the node's `build_chat_model` call, matching every other LLM-invoking executor (CLAUDE.md fix #6) — the initial implementation had silently omitted this.

**Consequences.**
- The designer's Jira panel (`frontend/.../node-panels/jira.tsx`) treats the redacted marker as "field unchanged": it renders the input empty with a "token is set — leave blank to keep, type to replace" placeholder rather than showing the literal dots as if they were typed, and only sends a new value when the user actually edits the field.
- This credential model is intentionally different from the LLM-key masking convention in `src/api/admin_llm_keys.py` (which shows a 6-char prefix) — a Jira API token has no legitimate reason to be partially shown back to the UI, so it's fully redacted rather than prefixed. `docs/admin-guide.md`'s LLM-keys section calls this out explicitly so admins don't expect a `jira` row in `/admin/llm-keys`.
- No workflows containing real Jira credentials existed before this fix shipped (the feature was still in review), so no backfill/migration script was written to re-encrypt pre-existing plaintext rows. If that assumption is ever wrong, `decrypt_jira_api_token`'s pass-through behavior means old plaintext rows keep working, but they remain unencrypted at rest until the workflow is next saved — worth revisiting if this ever matters in practice.

**Implemented by.** `src/engine/workflow.py` (alias fix + crypto helpers), `src/api/workflows.py` (encrypt-on-write / redact-on-read), `src/executors/jira.py` (server-side decrypt + LangSmith threading), `tests/unit/executors/test_jira_executor.py`, `tests/unit/api/test_workflows_jira_tokens.py` (commits on `main`, 2026-07-10).

**Related.** CLAUDE.md §1 (the six MCP/LLM fixes — LangSmith threading is fix #6); `src/security/encryption.py` (the AES-256-GCM primitive reused here).

## ADR-0029: Approve-via-email — signed one-click links, not reply-parsing

**Status.** Accepted 2026-07-11.

**Context.** A `user-approval` node's only decision path was in-app: open Composer, find the paused run, click Approve/Reject (`POST /executions/{id}/resume`). The user wanted a lower-friction path for reviewers who may not have (or want to open) a Composer session — email the approver directly and let them decide with a single click. Two designs were considered for how the email itself carries the decision back to the system: (1) parse the reviewer's reply email for an "approve"/"reject" keyword, or (2) embed pre-signed decision links in the outbound email and resolve whichever one is clicked. Separately, the user flagged that `waiting_approval` executions had no upper time bound at all — fine for compute (a paused execution is fully checkpointed, not running), but not for indefinite Postgres/checkpoint row growth from runs nobody ever resolves.

**Decision.**
- **Signed links over reply-parsing.** Reply-parsing requires inbound-email infrastructure Composer doesn't have (a receiving mailbox, a webhook from the email provider, MIME/thread parsing) and is trivially spoofable — anything that can send an email "From" the approver's address, or reply into the right thread, can forge a decision with no cryptographic binding to the actual node/execution. A signed link needs none of that: `create_approval_email_token` (`src/security/jwt.py`) mints a new JWT type (`type="approval_email"`) whose payload bakes in `sub` (execution id), `node_id`, `decision`, `approver_email`, and `pending_since` — the decision itself is part of the signed payload, not a mutable query parameter, so tampering with the URL invalidates the signature rather than flipping the outcome. Two tokens are minted per pause (one per possible decision), producing two distinct links in one email.
- **No login required, by design.** The signed token *is* the credential. `GET /approvals/email/{token}` (`resolve_approval_email` in `src/api/approval_email.py`) has no auth dependency at all — it verifies the token, confirms it matches the execution's *current* pause (both `node_id` and `pending_since`, so a `while`-loop node that pauses repeatedly at the same `node_id` can't have a stale token from an earlier iteration resolve a later one), atomically flips status via a conditional `update_many` (`WHERE id=... AND status='waiting_approval'`) so two near-simultaneous requests for the same pause — e.g. an email-security link-scanner prefetching both the Approve and Reject URLs — can't both win, records an `Approval` row (`approverEmail` set, `approverUserId` null, `viaEmailLink=true`), schedules `resume()` in the background, and redirects (303) to `{FRONTEND_URL}/approval-result?status=approved|rejected|invalid`. This intentionally lets a reviewer with no Composer account at all participate — the in-app path remains available and unaffected for reviewers who do have one.
- **The sweeper timeout is independent of the link TTL, on purpose.** The emailed link expires after `approval_link_ttl_hours` (default 72h) — a property of the token's own `exp` claim, set in `create_approval_email_token`. Separately, `sweep_expired_approvals` (`src/maintenance/execution_sweeper.py`) auto-fails any `waiting_approval` execution once `_pending_approval_since` (stamped into `variables` the moment the pause begins) is older than `approval_wait_timeout_hours` (default 168h / 7 days). These are deliberately two different numbers serving two different purposes: the link TTL bounds how long the *emailed shortcut* stays clickable (after which in-app approval still works fine), while the sweeper's much longer timeout bounds how long the *execution itself* is allowed to sit unresolved by any means before Composer gives up on it and reclaims the row. Collapsing them into one setting would either expire working in-app approvals too aggressively or let unresolved runs accumulate forever.
- **Paused executions hold no live compute — confirmed, not assumed.** The moment `LangGraphExecutor.run()`/`.resume()` detects a fresh pause, it persists `status='waiting_approval'` (plus `variables._pending_approval_node` and `_pending_approval_since`), emits the `approval_required` event, and returns — the async task exits completely. Every variable, every node's results so far, and exactly which node to resume at is serialized into the `LangGraphCheckpoint`/`LangGraphCheckpointWrite` Postgres tables via `PrismaCheckpointSaver`. A row waiting ten seconds and a row waiting ten days cost exactly the same: one idle Postgres row apiece. `sweep_expired_approvals` therefore exists purely for Postgres storage/row-growth hygiene and operational visibility, not to relieve any memory or process pressure — none exists between pause and resume.

**Consequences.**
- The `Approval` model's `approverUserId` had to become nullable, with new `approverEmail`/`viaEmailLink` columns, since an email-link decision has no authenticated Composer user behind it at all.
- `send_approval_email` (`src/engine/approval_email.py`) is called only from the one-shot "just detected a fresh pause" branches inside `LangGraphExecutor.run()`/`.resume()` — never from inside `UserApprovalExecutor` itself — because LangGraph replays a node's function body on resume, and a send placed before the `interrupt()` call would silently re-fire on every resume. `approver_email`/`approver_cc` substitution (pure, no side effect) stays inside the executor; the side-effecting send lives outside it.
- `approver_email` is opt-in per node: `send_approval_email` no-ops immediately if it's empty, so existing `user-approval` nodes with no email configured are unaffected and keep behaving exactly as before this feature shipped.
- Email delivery failures are logged, not raised — a Resend outage must not fail the workflow pause itself, since in-app approval remains available regardless of whether the email went out.
- No reminder/follow-up emails and no reply-body parsing are in scope; per-approver identity verification is deliberately limited to "possession of the emailed link," matching the no-login decision above.

**Update (2026-07-11): attachment support.** `user-approval` gained an optional `attachmentPath` field (`UserApprovalNodeData.attachment_path` in `src/engine/workflow.py`), substituted from workflow state the same way `approver_email`/`approver_cc` are — typically `{{lastOutput}}` from an upstream `file-write` node — and attached to the approval email by `_build_attachment` in `src/engine/approval_email.py`. It gets the same trust treatment as `file-write`'s own path fields (ADR-0030): a value substituted from workflow state can carry untrusted upstream node output (LLM/agent/HTTP/MCP results), so it is never trusted to point anywhere readable on the server. Instead it's bounded to a configured root directory (new `approval_attachment_root` setting, default `/tmp/composer-attachments`) via a `Path.resolve()` containment check, and capped at a configured size (new `approval_attachment_max_bytes` setting, default 10 MiB) to bound the base64-encode and email payload size. Every failure mode — outside the root, missing file, oversized, or any other read/encode error — is a skip-with-warning rather than a raise, consistent with this ADR's existing "email delivery failures are logged, not raised" consequence above: an attachment problem must never be allowed to prevent the approval email (and the decision it carries) from going out.

**Implemented by.** `src/security/jwt.py` (`create_approval_email_token`/`verify_approval_email_token`, `type="approval_email"`), `src/engine/workflow.py` (`UserApprovalNodeData.approver_email`/`approver_cc`/`attachment_path`), `src/executors/user_approval.py` (folds substituted values into the `interrupt()` payload), `src/engine/approval_email.py` (`send_approval_email`, `_build_attachment`), `src/engine/langgraph_executor.py` (call sites in `run()`/`.resume()`, `_pending_approval_since` stamping), `src/api/approval_email.py` (`resolve_approval_email`, public `GET /approvals/email/{token}`), `src/maintenance/execution_sweeper.py` (`sweep_expired_approvals`), `src/config.py` (`backend_public_url`, `approval_link_ttl_hours`, `approval_wait_timeout_hours`, `rate_limit_approval_email_per_minute`, `approval_attachment_root`, `approval_attachment_max_bytes`), `prisma/schema.prisma` (`Approval.approverEmail`/`viaEmailLink`, nullable `approverUserId`), `frontend/components/composer/canvas/node-panels/user-approval.tsx` (Approver email/CC/Attachment path fields), `frontend/app/(auth)/approval-result/page.tsx` (unauthenticated confirmation page); tests in `tests/unit/security/test_jwt.py`, `tests/unit/engine/test_approval_email.py`, `tests/unit/engine/test_langgraph_executor.py`, `tests/unit/api/test_approval_email.py`, `tests/unit/maintenance/test_execution_sweeper.py` (commits on `main`, 2026-07-11).

**Related.** `docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md` (full design record); ADR-0027 (reuses the same short-lived-JWT-plus-email-link pattern established for password reset, `password_change` token type); CLAUDE.md's Phase 5a note on `interrupt()`/`resume()` (the underlying pause mechanism this feature notifies about).

## ADR-0030: File storage provider framework — pluggable, local-first, inline-per-node config

**Status.** Accepted 2026-07-11.

**Context.** Two new capabilities were requested that share the same underlying shape: (1) a node that notices a new file dropped into a folder and kicks off a workflow, moving the file once it's been claimed; (2) a node that writes generated content (e.g. a drafted BRD) out to a file — as Markdown, Word, or PDF — mid-flow. Both need the same abstraction over "somewhere files live," designed against a `FileStorageProvider` interface (`src/storage_providers/base.py`) with a local-filesystem implementation now (`LocalFilesystemProvider`) and S3/Google Drive/OneDrive left as documented, not-yet-built extension points behind the same interface.

**Decision.**
- **`file-trigger` is visual-only, not a real execution-graph node.** It mirrors the existing precedent set by `note` (`NoteNodeData`/`NoteNode`, "visual-only; executor is a no-op; graph_builder skips") — `graph_builder.py`'s `_VISUAL_ONLY_TYPES` set now contains both `{"note", "file-trigger"}`. The actual folder-watching runs as a separate long-lived process (`composer watch`, `src/cli/watch.py`), not as a step the execution engine advances through, because "poll a folder forever" has no place inside a single workflow *run* — a run has a start and an end; a watcher does not. The node's only job is to be a canvas-visible configuration surface (`provider`, `sourcePath`, `destPath`, `errorPath`, `targetInputVariable`, `pollIntervalSeconds`) whose settings an operator reads off to configure the CLI invocation.
- **`composer watch` reuses `POST /api/run/{slug}`, no new backend trigger endpoint.** The external-invoke endpoint already accepts a bearer `ck_...` API key, resolves a **production** workflow, and accepts a JSON `{input: {...}}` body, entirely independent of any interactive Composer session — exactly the shape an automated, non-interactive trigger needs. Building a parallel `/triggers/file` endpoint would duplicate auth, workflow resolution, and execution-kickoff logic that already exists and is already tested. The trade-off: `file-trigger`'s workflow must be published (`isProduction=true` + `externalSlug`) before the CLI can drive it — acceptable, since a file-triggered automation is by definition meant to run unattended in production, not as a draft.
- **DOCX/PDF conversion is pure-Python (`markdown-it-py` + `python-docx` + `xhtml2pdf`), not pandoc-based.** A pandoc-based renderer would need a system binary installed in the container image — the existing multi-stage Docker build is deliberately kept dependency-light, and adding a non-Python toolchain purely for occasional document export was judged not worth that cost. `markdown-it-py` (new dependency) parses CommonMark; `python-docx` (already a dependency, previously only used for reading via `/uploads/extract-text`) renders DOCX; `xhtml2pdf` (new dependency) renders the HTML `markdown-it-py` produces into PDF. Both new renderers (`src/conversion/markdown_to_docx.py`, `src/conversion/markdown_to_pdf.py`) are purpose-built for the structured, table-heavy document shape this feature targets (BRDs and similar) — headings, paragraphs, lists, tables, bold/italic/inline-code — not general-purpose, pixel-perfect Markdown fidelity (footnotes, nested blockquotes, embedded images are out of scope).
- **Storage config is inline-per-node, not a shared named-connection registry.** `sourcePath`/`destPath`/`errorPath` on `file-trigger` and `destinationPath` on `file-write` live directly in the node's data, the same way Jira credentials live directly on the `jira` node (ADR-0028) rather than in a shared MCP-style registration. A shared "storage connection" entity was considered and rejected: file paths (today) carry no secret worth centralizing and managing access to, and inline config keeps a `file-write` node fully self-describing from the workflow JSON alone, with no cross-reference to resolve at execution time.

Two rounds of security hardening followed the initial implementation, both because `file-write`'s `filename`/`destinationPath`/`content` fields are populated via `{{variable}}` substitution from upstream node output — LLM/agent/HTTP/MCP results the workflow author doesn't fully control the shape of:
- **Path-traversal and empty-destination rejection.** `FileWriteExecutor` now raises `InvalidFilenameError` if the substituted `filename` contains `/`, `\`, or `..`, or is blank — a bare filename never legitimately needs path segments; a workflow wanting a nested output directory should express that via `destinationPath` instead. It raises `InvalidDestinationError` if the substituted `destinationPath` resolves to blank — `Path("")` resolves to the server process's current working directory, and silently accepting that would let substituted content decide where on disk the server writes. Both checks run, and both errors raise, before any bytes are written.
- **SSRF-safe PDF export and disabled HTML passthrough.** `markdown_to_pdf`'s call into `xhtml2pdf.pisa.CreatePDF` passes `link_callback=_deny_all_resources`, a deny-all callback wired into every resource resolution (`<img src>`, CSS backgrounds, `@font-face`) reachable from the rendered HTML — without it, xhtml2pdf performs a real, unguarded outbound HTTP GET (no timeout override, no size cap, no destination allowlist) for any `http(s)://` reference it finds, so a workflow rendering attacker-influenced Markdown containing e.g. `![x](http://169.254.169.254/latest/meta-data/)` could be turned into an SSRF probe purely by being exported to PDF — the same class of issue already closed for the HTTP node executor (`src/security/ssrf.py`, commit `999c5ac`). Separately, both `markdown_to_docx` and `markdown_to_pdf` construct their `MarkdownIt` parser with `MarkdownIt("commonmark", {"html": False})`, disabling CommonMark's raw-HTML passthrough — without it, bracket placeholders extremely common in BRD-style documents (`<Client Name>`, `<INSERT DATE>`) parse as unrecognized inline/block HTML tags and get silently dropped by the renderer, instead of appearing as the literal text the document author intended.

**Consequences.**
- `file-trigger` needing a published, production workflow means it cannot be exercised via "Run Draft" the way every other node type can — an operator has to publish first, then run `composer watch` from a separate machine/process to see it do anything. This is a deliberate, documented limitation (see `docs/designer-guide.md`'s `file-trigger` section), not an oversight.
- Because storage config is inline rather than centrally registered, there's no admin-level view of "which workflows read/write which folders" — acceptable today since the only provider is the local filesystem and the blast radius of a misconfigured path is a single workflow's own node. Revisit if/when a credentialed provider (S3, Drive, OneDrive) is added, since those would carry real secrets worth centralizing.
- The DOCX/PDF renderers' purpose-built scope means arbitrary Markdown (deeply nested lists, footnotes, embedded images) will render incompletely or oddly — expected, not a bug, given the BRD-shaped target use case.
- `python-docx` and `pypdf` were already dependencies (used for reading uploads); only `markdown-it-py` and `xhtml2pdf` are net-new.

**Implemented by.** `src/storage_providers/base.py` (`FileStorageProvider` ABC, `FileRef`, `HealthStatus`), `src/storage_providers/local.py` (`LocalFilesystemProvider`), `src/engine/workflow.py` (`FileTriggerNodeData`/`FileTriggerNode`, `FileWriteNodeData`/`FileWriteNode`), `src/engine/graph_builder.py` (`_VISUAL_ONLY_TYPES`), `src/executors/file_write.py` (`FileWriteExecutor`, `InvalidFilenameError`, `InvalidDestinationError`, `UnknownStorageProviderError`), `src/conversion/markdown_to_docx.py`, `src/conversion/markdown_to_pdf.py` (`_deny_all_resources`), `src/cli/watch.py` + `src/cli/main.py`'s `watch` subcommand, frontend `node-panels/file-trigger.tsx` / `node-panels/file-write.tsx` + the usual palette/visuals/canvas-type registries; `pyproject.toml` (`markdown-it-py`, `xhtml2pdf`); tests across `tests/unit/storage_providers/`, `tests/unit/executors/test_file_write.py`, `tests/unit/conversion/`, `tests/unit/cli/test_watch.py` (commits on `main`, 2026-07-11).

**Related.** `docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md` (full design record); `docs/archive/phase-history/plans/2026-07-11-file-storage-provider-framework-plan.md` (10-task implementation plan); ADR-0028 (the inline-per-node credential precedent this reuses for `destinationPath`/`sourcePath`); `src/security/ssrf.py` + commit `999c5ac` (the HTTP-node SSRF guard this feature's PDF-export deny-all mirrors).

## ADR-0031: Persistence stack — stay on Prisma Python, don't migrate to SQLAlchemy 2 (yet)

**Status.** Accepted 2026-07-13.

**Context.** `docs/claude-improvement-backlog.md` §P3-1 flagged Prisma Client Python as the highest structural risk in the backend stack: its upstream GitHub repository was reported archived. The item explicitly scopes itself as "an ADR and proof-of-concept task first — not authorization for a repository-wide ORM rewrite," and lists eight things to evaluate before any migration decision: maintenance/ecosystem risk, async support, type safety/schema generation, transactions/row-locking/leases/worker-claims, migration tooling, LangGraph checkpoint integration, an incremental migration strategy, and regression risk/operational cost. This ADR reports findings against each, backed by direct verification rather than accepting the audit's claims at face value, per the audit doc's own "validate the backlog against the code rather than accepting it blindly" instruction.

**Findings.**

- **Maintenance/ecosystem risk — confirmed, and worse than "reported."** `gh api repos/RobertCraigie/prisma-client-py` shows `archived: true`, last push `2025-04-10`, 252 open issues, zero commits since. This is real, ongoing risk: no security patches, no bug fixes, no compatibility updates for future Python or Postgres releases. Nothing found during this evaluation reduces that risk — it's the one finding that doesn't get better with more digging.
- **Async support — a non-issue.** Composer's entire codebase already runs Prisma Python async-natively (`await db.workflow.find_unique(...)` etc. throughout `src/`). SQLAlchemy 2's async engine is equally mature (native since 2.0, asyncpg driver). No difference in outcome, just in API shape.
- **Type safety and schema generation — a real paradigm difference, not a defect either way.** Prisma is schema-first: `schema.prisma` is the single source of truth, and `prisma generate` produces a fully-typed client from it — the workflow this session used repeatedly (P1-3's `idempotencyKey` migration: edit `schema.prisma`, `prisma migrate dev`/`deploy`, `prisma generate`, typed client immediately reflects the new field). SQLAlchemy 2 is code-first: declarative model classes *are* the schema, and Alembic's `--autogenerate` diffs against them to produce migrations. Migrating means rewriting all 14 models (`grep -c "^model " prisma/schema.prisma`) as SQLAlchemy declarative classes — a real, bounded-but-substantial rewrite, not a config change.
- **Transactions, row locking, leases, worker claims — verified achievable today, not a blocker.** This was the finding expected to be the strongest case *for* migrating, since P1-2 (durable workers, itself deferred) needs exactly this. Built and ran `scripts/poc_persistence_row_lock.py` against the real dev Postgres instance: two concurrent Prisma client connections each open `db.tx()`, run `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` via `query_raw`, hold the lock, then commit an `UPDATE` via `execute_raw`. Result: **two distinct rows claimed, zero double-claims**, confirming Prisma Python's raw-SQL escape hatch (`query_raw`/`execute_raw`/`tx()`) gives real Postgres row-level locking semantics, not just ORM-level optimistic concurrency. Separately, the atomic-conditional-transition pattern this session used repeatedly and shipped to production (P1-1's scanner-safe approval, P1-3's idempotency-key race guard, P1-6's cancel-vs-resume race guard — all via `update_many(where={id, status: X}, data={status: Y})` + checking the affected-row count) already covers Composer's actual current concurrency-safety needs without touching raw SQL at all, because a single `UPDATE ... WHERE` statement is inherently atomic in Postgres.
- **Migration tooling — both actively maintained; Alembic's async story has real but bounded friction.** Prisma Migrate is in active use this session (`prisma/migrations/20260712150606_add_execution_idempotency_key/`) and works correctly. Alembic is independently and currently maintained (`gh`/web search: 1.18.5, released 2026-06-25) with native async support via `run_async()`, though its default generated `env.py` doesn't support async drivers out of the box and needs custom configuration for `asyncpg` — a known, documented, solvable friction point, not a dead end.
- **LangGraph checkpoint integration — the coupling to Prisma is narrow and was already designed with an escape hatch.** `PrismaCheckpointSaver` (`src/storage/checkpointer.py`) implements exactly four `BaseCheckpointSaver` async methods (`aget_tuple`, `alist`, `aput`, `aput_writes`) plus deletion, all against two tables, using only basic Prisma CRUD (`find_unique`, `find_many`, `upsert`, `delete_many`) — no exotic Prisma-only features. ADR-0001 (the original decision to build this custom saver instead of adopting `langgraph-checkpoint-postgres`) explicitly named this as a reversible choice: *"If Prisma's BYTEA handling or async driver proves problematic, the escape hatch is to switch to Option A."* Migrating this one file to SQLAlchemy (or to the official `langgraph-checkpoint-postgres` package) is a self-contained, bounded task regardless of what happens to the other 13 models.
- **A genuine Prisma schema-expressiveness gap, found as a side effect.** `checkpointer.py`'s own docstring records that Prisma "doesn't support composite-PK FKs in schema.prisma" — `adelete_thread()` has to manually enforce writes-before-checkpoints delete ordering in application code because the schema can't express that FK constraint declaratively. SQLAlchemy wouldn't have this specific limitation. A real point in SQLAlchemy's favor, though the current mitigation (one documented, tested, working method) fully closes the gap in practice.
- **Regression risk and operational cost — currently prohibitive relative to the risk being mitigated.** 14 Prisma models, 30 files with direct `db.<model>.` call sites across `src/`, 1000+ passing tests whose fixtures and assertions are shaped around Prisma's query/response conventions (`MagicMock`/`AsyncMock` on `db.workflowexecution.find_unique` etc. throughout `tests/unit/`). A full migration is a genuine multi-week, high-touch rewrite — exactly the "repository-wide ORM rewrite" the backlog item says this ADR does *not* authorize.

**Decision.** **Stay on Prisma Python. Do not begin a migration.** The archived-repo risk is real and doesn't improve with more analysis, but every concrete technical requirement this evaluation set out to test — including the one (row-locking/worker-claims) expected to be the strongest argument for migrating — is achievable today via Prisma's documented raw-SQL escape hatch, verified against a real database rather than assumed. A multi-week, 30-file, 14-model rewrite is not justified by risk that hasn't yet materialized into an actual blocked feature or an actual broken upgrade.

Concrete, monitorable trigger conditions for revisiting this decision:
1. Prisma Python breaks against a future Python or Postgres minor/major version, with no fix possible because the repo is archived.
2. A genuine feature requirement is hit that even `query_raw`/`execute_raw`/`db.tx()` cannot route around.
3. P1-2 (durable workers), when actually designed and built, finds the raw-SQL-on-Prisma pattern this ADR validated too unergonomic to build a real worker-claim/lease/heartbeat system on top of, in practice rather than in a 100-line POC.

If any of those trigger, the recommended path is **incremental, not big-bang**: introduce SQLAlchemy 2 + Alembic scoped to *new* tables only — P1-2's worker-queue/lease tables have no legacy data or existing call sites to migrate, so they're a natural, low-risk place to prove SQLAlchemy in this codebase for real before ever touching the other 13 Prisma models. `PrismaCheckpointSaver`'s narrow, already-escape-hatched coupling (ADR-0001) would be the second, similarly bounded candidate.

**Consequences.**
- No code changes ship from this ADR beyond the POC script and this record — per the backlog item's own constraint, no production migration begins without a separate, explicit review.
- `pyproject.toml`'s `prisma>=0.15.0` pin stays as-is; no new dependency added.
- Future work that touches the persistence layer (P1-2 durable workers in particular) should read this ADR first — the row-locking pattern it validated is the one to reuse, via `db.tx()` + `query_raw`/`execute_raw`, rather than re-litigating whether Prisma can support it.
- This finding should be revisited periodically (e.g., alongside P2-4's dependency-reproducibility work) even absent a trigger condition, since "archived repo, still works" is a risk that only ever gets worse with time, never better.

**Implemented by.** `scripts/poc_persistence_row_lock.py` (the row-locking POC; safe to re-run against any dev database — writes and cleans up only its own scratch rows). No other code changes.

**Related.** `docs/claude-improvement-backlog.md` §P3-1 (the source backlog item); `docs/deferred-backlog.md` (P3-1's entry, now resolved by this ADR); ADR-0001 (the checkpoint-saver escape-hatch precedent this ADR's incremental-migration recommendation reuses); `docs/claude-improvement-backlog.md` §P1-2 (durable workers — the backlog item most likely to trigger revisiting this decision) and §P2-4 (dependency reproducibility — the audit's own recommended sequencing put this persistence decision ahead of routine dependency upgrades).

## ADR-0032: P0-5 disclosure gap — extend Jira's encrypt/redact pattern generically, defer the Credential model

**Status.** Accepted 2026-07-13.

**Context.** `docs/claude-improvement-backlog.md` §P0-5 found that Jira's `apiToken` is the only credential field with encryption-at-rest and read-redaction (ADR-0028) — vector-db `apiKey`/`embeddingApiKey`, HTTP node `httpHeaders`, and MCP server `headers`/`oauthConfig.clientSecret` are all returned in plaintext by their respective read APIs today. Verifying against the code (not just the audit text) confirmed this precisely: `GET /workflows`, `/workflows/search`, and `/workflows/{id}` return `vectorDbApiKey`/`vectorDbEmbeddingApiKey`/`httpHeaders` verbatim; `GET /mcp-servers` returns `headers` verbatim; `McpServer.oauthConfig.clientSecret` is written to Postgres as plaintext JSON on both create and the `oauth-config` PATCH. The `isShared` flag (CLAUDE.md §1 fix #5) means a shared MCP server's plaintext headers are visible to every user who can see that server, not just its creator.

The full P0-5 objective — "one secure credential abstraction shared by all integrations," a first-class `Credential`/`Connection` model with rotation, ownership, and reuse (needed by P2-2) — is a multi-week product feature with real design questions (sharing model, rotation UX, migration path for existing inline credentials). The user explicitly chose to split this: patch the live disclosure gap now using the pattern already proven for Jira, defer the model decision (see `docs/deferred-backlog.md`'s updated P0-5 entry).

**Decision.** Extend Jira's exact encrypt-at-rest + redact-on-read treatment to the three remaining gaps, via **generic, reusable primitives** rather than three more copy-pasted implementations:
- `src/security/encryption.py` gained `encrypt_marked`/`decrypt_marked`/`is_marked_encrypted` (same `enc:v1:` prefix Jira's `JIRA_TOKEN_ENC_PREFIX` already uses — wire-compatible, not just parallel) for single-string fields, and `redact_sensitive_headers`/`encrypt_sensitive_headers`/`decrypt_sensitive_headers` (keyed off a shared `SENSITIVE_HEADER_NAMES` set — `authorization`, `x-api-key`, `cookie`, etc., mirroring the existing `_SENSITIVE_QUERY_PARAMS` convention from the HTTP-node SSRF work, P0-6) for header-dict fields, where most entries (`Content-Type`, `Accept`) are not secret but a few commonly are.
- Vector-db `apiKey`/`embeddingApiKey`: `src/api/workflows.py` gained `_redact_vector_db_keys`/`_encrypt_vector_db_keys`, wired into the same `_to_workflow_read`/create/update call sites `_redact_jira_tokens`/`_encrypt_jira_tokens` already use. `VectorDbExecutor` decrypts at all three read sites (`_run_query`, `_run_upsert`, `_embed`) before variable substitution.
- HTTP `httpHeaders`: same `workflows.py` treatment (`_redact_http_headers`/`_encrypt_http_headers`), applied per-header-name rather than a fixed field. `HttpExecutor` decrypts before substitution.
- MCP `headers` and `oauthConfig.clientSecret`: `src/api/mcp_servers.py`'s `_to_read` (the single function every read path already funnels through) redacts headers; `create_mcp_server` and `update_oauth_config` encrypt both fields on write. `src/mcp/oauth.py`'s `exchange_code_for_tokens` and `refresh_token` decrypt `clientSecret` via `decrypt_marked` before building the token-exchange form — `decrypt_marked`'s pass-through-on-unmarked guarantee means servers configured before this fix keep working with no backfill.

**Consequences.**
- Decrypt always happens *before* `{{variable}}` substitution, not after — a decrypted value might itself be a `{{var}}` template (e.g., a workflow author referencing a Set State value instead of a literal secret); encryption preserves the exact string, so decrypt-then-substitute is correct regardless of which case it was.
- MCP `headers` currently has no consumer that builds outbound requests from it (`MCPClient` only accepts a single `auth_header`, built separately from `authType`/`encryptedAccessToken`) — confirmed by grep, not assumed. Redact/encrypt were still applied since the field is disclosure-reachable via `GET /mcp-servers` regardless of whether anything consumes it yet; a comment at the redact/encrypt call sites flags that any future code reading `server.headers` for a real request must decrypt via `decrypt_sensitive_headers` first.
- No schema migration — same JSON-column, marker-prefixed approach Jira already uses, so this ships without touching `prisma/schema.prisma` or coordinating with the concurrent session's active schema work.
- The `Credential`/`Connection` model itself remains deferred; this ADR only closes the disclosure gap the audit specifically evidenced, not P0-5's full objective.

**Implemented by.** `src/security/encryption.py`, `src/api/workflows.py`, `src/executors/vector_db.py`, `src/executors/http.py`, `src/api/mcp_servers.py`, `src/mcp/oauth.py`; tests in `tests/unit/security/test_encryption.py`, `tests/unit/api/test_workflows_vector_db_keys.py` (new), `tests/unit/api/test_workflows_http_headers.py` (new), `tests/unit/executors/test_vector_db.py`, `tests/unit/executors/test_http_executor.py`, `tests/unit/api/test_mcp_servers.py`, `tests/unit/api/test_mcp_servers_oauth.py`, `tests/unit/mcp/test_oauth_exchange.py` (commits on `main`, 2026-07-13).

**Related.** ADR-0028 (the Jira pattern this generalizes); `docs/claude-improvement-backlog.md` §P0-5 and §P0-6 (`_SENSITIVE_QUERY_PARAMS`, the precedent for `SENSITIVE_HEADER_NAMES`); `docs/deferred-backlog.md` (updated P0-5 entry — model decision still open).

## ADR-0033: Durable execution + shared events/rate-limits — Cloud Tasks + Postgres

**Status.** Accepted 2026-07-13, implemented 2026-07-15. User approved: Cloud Tasks as new infrastructure, full replacement of the in-process execution model (not run-alongside), and P1-2 + P1-4 implemented together. Implementation plan: `docs/superpowers/plans/2026-07-13-durable-execution-cloud-tasks.md` (16 tasks, TDD) — all 16 complete, 41 commits, merged into `main` 2026-07-15 (docs/decisions.md's own two independently-edited versions reconciled as part of the merge — see "Known unresolved item at completion" below, now resolved). See the 2026-07-14 addendum below for a cost-driven refinement to Task 11/14's scope, made mid-implementation, and the 2026-07-15 addendum for a completion summary (deviations, the two production bugs integration testing and a final holistic review caught).

**Context.** Two related backlog items (P1-2, P1-4) both stem from the same root cause: Composer's execution model and cross-cutting infrastructure (event bus, rate limiter) are process-local, correct only for a single, continuously-running backend instance.

Verified against the actual deployment config (`scripts/gcp-bootstrap.ps1`), not assumed: Cloud Run runs with `--min-instances=0 --max-instances=3`. This means the risk P1-2 describes is **active today, not theoretical** — every execution is launched via `BackgroundTasks.add_task()` (`src/api/executions.py`, `src/api/approval_email.py`) or a bare `asyncio.create_task()` (`src/api/run.py`), all of which run *after* the HTTP response is sent. Cloud Run's autoscaler doesn't track work that continues past the response — with `min-instances=0`, an idle instance (no in-flight *requests*) is eligible for scale-down regardless of whether a detached background task is still running. Confirmed via GCP's own developer-forum guidance (multiple threads on this exact FastAPI/Cloud-Run pattern): Google's own recommended fix for this exact failure mode is "use Cloud Tasks" or "do the work inside the request." P1-4's gap is real too but lower-severity at Composer's current scale (`--max-instances=3`, a handful of internal Bounteous users) — multi-instance concurrency is possible, not constant.

**Options considered** (per the backlog's required comparison):

| Option | Fit |
|---|---|
| **Google Cloud Tasks** | Purpose-built GCP-native task queue with HTTP-push delivery, built-in retry/backoff, rate limiting, and dead-letter queues — the exact primitives P1-2's acceptance criteria require (lease/heartbeat, bounded retry, dead-letter). Push delivery means the worker *is* an inbound HTTP request, which Cloud Run won't scale to zero mid-flight — directly solves the root cause. Confirmed current (`google-cloud-tasks` PyPI package, actively documented June–July 2026) with a standard OIDC-authenticated Cloud-Run-target pattern. New GCP service to provision, but zero new vendor relationship (same project, same IAM boundary Composer already operates in). |
| **Google Pub/Sub** | Also GCP-native, but a general pub/sub system rather than a purpose-built task queue — using it as a durable job queue means hand-building the ack/nack/redelivery/dead-letter semantics Cloud Tasks gives for free. Better natural fit for fan-out (P1-4) than for a work queue (P1-2). Would mean maintaining two GCP messaging services (Pub/Sub + implicitly Cloud Tasks-shaped logic anyway) instead of one purpose-fit one. |
| **Postgres-backed queue** | Zero new infrastructure — reuses the existing Neon Postgres already holding LangGraph checkpoints and execution rows. **Already proven correct for the core claim primitive**: `scripts/poc_persistence_row_lock.py` (ADR-0031) validated `SELECT ... FOR UPDATE SKIP LOCKED` via Prisma's raw-SQL escape hatch against two genuinely concurrent workers, zero double-claims. The unresolved problem is *triggering*: a Postgres-only queue still needs something to periodically claim work, and a polling loop tied to the FastAPI app's lifespan (the existing `execution_sweeper.py` pattern) has the *same* scale-to-zero exposure this ADR exists to fix — it only ticks while an instance happens to be alive. |
| **Redis-backed workers** (Celery/RQ/arq) | Well-trodden pattern with built-in lease/retry primitives, but a **brand-new infrastructure dependency** Composer has never used — a new managed Redis instance (GCP Memorystore or equivalent), new connection string, new failure mode to monitor, new vendor surface. Same "who triggers the worker" problem as pure Postgres, without the offsetting benefit of reusing infrastructure Composer already runs. Weakest fit against "prefer the smallest reliable option." |

**Recommendation.** A hybrid, using each piece of infrastructure for what it's actually best at, adding exactly one new managed service (Cloud Tasks) rather than two:

- **P1-2 (durable execution):** Cloud Tasks triggers a new authenticated HTTP endpoint (e.g. `POST /internal/claim-and-run`) via OIDC, one task per execution. Because it's a real inbound request, Cloud Run keeps the instance alive for its duration — solving the root cause directly. Postgres remains the source of truth and the actual concurrency guard: the endpoint still does `SELECT ... FOR UPDATE SKIP LOCKED` (the already-validated pattern) before running, so even if Cloud Tasks's at-least-once delivery redelivers a task, only one worker can win the claim — satisfying "two workers cannot execute the same lease concurrently" without trusting Cloud Tasks alone for that guarantee. Cloud Tasks' native retry/backoff/dead-letter config covers "recover expired leases with bounded retry" and "dead-letter handling" without hand-building either. Approval resume (`POST /executions/{id}/resume`) routes through the identical mechanism — enqueue a task instead of `BackgroundTasks.add_task()`.
- **P1-4 (events):** Postgres `LISTEN/NOTIFY`, no new infrastructure. Verified characteristics (not assumed): 8000-byte payload cap, transactionally consistent with commits, but **not durable** — a notification is lost if no one is listening at emit time. Design accordingly: `NOTIFY` payload carries only a small pointer (execution ID + sequence number), and the actual event history lives in a new persisted table, giving reconnect-cursor and missed-terminal-event recovery (the requirements `LISTEN/NOTIFY` alone can't satisfy) while `LISTEN/NOTIFY` itself only serves as the low-latency "something changed, go look" signal to already-connected WebSocket handlers.
- **P1-4 (rate limits):** A Postgres table with the same atomic-conditional-update pattern already shipped three times this session (P1-1, P1-3, P1-6) — a single `UPDATE ... WHERE tokens >= cost RETURNING tokens` is inherently atomic in Postgres, giving cross-instance-correct token buckets with no new infrastructure at all.

**Why not defer further or pick something smaller still:** the risk is confirmed active (not latent) given `min-instances=0`, and Google's own guidance for this exact pattern independently converges on the same answer (Cloud Tasks) this analysis reached from Composer's specific constraints.

**Open questions for the user before implementation starts:**
1. Approve introducing Cloud Tasks as new infrastructure (a new GCP service + IAM/OIDC configuration + a new `pyproject.toml` dependency, `google-cloud-tasks`)?
2. Does this replace `LangGraphExecutor.run()`'s current in-process invocation entirely, or run alongside it for new executions while in-flight ones drain out under the old model?
3. Rollout scope — implement P1-2 and P1-4 together as this ADR frames them, or land P1-2 first (the higher-severity, confirmed-active risk) and revisit P1-4 separately?

**Consequences (if approved).**
- New `pyproject.toml` dependency: `google-cloud-tasks`.
- New config surface: GCP project ID, Cloud Tasks queue name/region, a service-account for OIDC — none of which exist in `src/config.py` today.
- New Prisma models: an events table (P1-4) and lease/heartbeat columns on `WorkflowExecution` or a companion table (P1-2) — schema migrations required, unlike ADR-0031/0032 which deliberately avoided them.
- `execution_sweeper.py`'s existing stuck-execution sweep either gets replaced by lease-expiry recovery or continues as a secondary safety net — needs an explicit decision during implementation, not assumed here.
- `docs/architecture.md` and the deployment docs need updates once implemented (new GCP service in the request path).

**Addendum (2026-07-14) — cost review, sweeper redesign.** Mid-implementation, the user (self-funding a POC/POV, not yet production) raised a direct cost concern about this ADR's design. Investigation of the actual deployed config (`scripts/gcp-bootstrap.ps1`) found the real dominant cost driver was **not** Cloud Tasks itself:

- Cloud Tasks holding a Cloud Run instance alive for an execution's duration bills the same per-second rate as the work already takes — it converts "work that sometimes dies early to scale-down and is under-billed because it never finishes" into "work that reliably completes and is billed for its real duration." Bounded by `--max-instances=3`; not an open-ended cost increase.
- `--no-cpu-throttling` (CPU always allocated), already present on the live `composer-backend` service *before* this ADR, exists solely to keep `src/maintenance/execution_sweeper.py`'s in-process `while True: ... await asyncio.sleep()` loop ticking between requests. Cloud Run bills CPU-always-allocated continuously for as long as any instance is up, independent of request activity — this is the actual ongoing cost driver, and this ADR's original Task 11 (extend the sweeper for lease-expiry recovery) would have made Composer permanently dependent on keeping this flag set.

**Decision:** keep the Cloud Tasks approach (P1-2) as designed. Revise Task 11's scope: convert the sweeper from an in-process background loop to a Cloud Scheduler-triggered HTTP endpoint (a genuine inbound request on a fixed interval), removing the need for `--no-cpu-throttling` entirely. Task 14's deploy-script work must not re-add the flag.

**Interim action taken immediately (ahead of Task 11 landing):** `--no-cpu-throttling` removed from `scripts/gcp-bootstrap.ps1`'s backend deploy args, and applied live via `gcloud run services update composer-backend --cpu-throttling` (revision `composer-backend-00088-v2f`, confirmed `run.googleapis.com/cpu-throttling` annotation flipped `false` → `true`). Disclosed, accepted tradeoff: until Task 11's redesign lands, the in-process sweeper only actually executes when a live request happens to wake the instance, rather than reliably every `EXECUTION_SWEEPER_INTERVAL_SECONDS` — stuck-execution cleanup may lag during idle periods. Acceptable at POC/POV traffic levels; revisit before production load.

**Addendum (2026-07-15) — completion summary.** All 16 tasks landed (41 commits; see Implemented by below). Notable deviations/discoveries beyond the Task 11 sweeper redesign above:

- **A real production bug, caught only by integration testing (Task 15).** `claim_and_run`'s raw-SQL lease-`UPDATE` sent a Python `datetime` as `text`; Postgres rejected it against the `timestamp without time zone` column (fixed in `a0bab20` with an explicit `$2::timestamptz` cast). No unit test could have caught this — every unit test mocks the DB; only the real-Postgres integration test added in this same task (`aa458d0`) exercised the actual parameter binding. Investigating it surfaced a broader, still-open finding: **no** `DateTime` column in `prisma/schema.prisma` uses `@db.Timestamptz` anywhere in the app, so every `DateTime` write, not just this one, is silently contingent on the Postgres session timezone staying UTC/GMT (confirmed true today on Neon, not guaranteed forever). Logged as a fast-follow in the plan doc (`cc9ab19`) rather than fixed here — pinning the session timezone or migrating the schema is out of scope for this ADR's acceptance criteria.
- **A second real production bug, caught only by a final holistic review of the whole branch (not any single task's review).** `resume_execution` (`src/api/executions.py`) and the emailed-approval confirm endpoint (`src/api/approval_email.py`) flipped `status: waiting_approval → running` and enqueued a fresh Cloud Task without clearing the `leaseOwner`/`leaseExpiresAt` set by the *original* `claim_and_run` claim — `_mark_waiting_approval` never touches the lease columns, so it survives the entire approval-pending window. Since the fresh Cloud Task's claim attempt requires the lease to be `NULL` or expired, any approval resolved within `execution_lease_seconds` of the original claim (default 3600s — i.e. almost always) silently found nothing to claim and stalled until `sweep_expired_leases` eventually noticed, up to an hour later. Fixed in `e270c542d92e517e2ad6e452e37e6328befac046` — both `update_many` calls now clear the lease fields in the same atomic statement that flips status; reproduced and proven fixed end-to-end against real Postgres in a new integration test (`test_resume_clears_stale_lease_and_reclaim_succeeds`). This bug survived per-task review specifically because it only manifests across the claim → pause → resume → reclaim round trip, which no single task's scope covered in isolation — worth noting as a process lesson: a final, whole-branch review pass caught something 16 individually-thorough task reviews did not.
- **Several narrowly-scoped fast-follow items were logged directly in the plan doc** rather than allowed to block task completion: a shared `CLAIMABLE_STATUSES` constant plus a genuine concurrency integration test (Task 10, `dac3099`); `SweepResult.recovered` field plus an errored-count asymmetry (Task 11, `057b660`); the sweeper's test-coverage claim plus `ANY()` array-binding correctness (Task 12, `5453b01`); the `DateTime`/timezone finding above (Task 15, `cc9ab19`). None block merge; each is a candidate for a small standalone follow-up, not yet promoted to `docs/deferred-backlog.md`.
- **This plan required substantially more review/fix cycles than a typical Composer feature of similar size**, proportionate to what `claim-and-run` actually is: the one endpoint responsible for guaranteeing an execution runs exactly once despite Cloud Tasks' at-least-once delivery — a security- and data-loss-sensitive surface where a subtle bug (a missing status filter, a race in the fast-fail write, an unguarded concurrent cancel, a stale lease blocking a legitimate reclaim) means either a silently double-run workflow (duplicate Jira tickets, duplicate emails) or a silently stalled/lost one. Most tasks in the 9-13/15 range shipped a `feat` commit followed by one or more same-review-pass `fix` commits rather than landing clean on the first pass (see Implemented by below) — treated as the expected cost of that sensitivity, not a process failure.

**Main/branch divergence — resolved at merge.** This branch forked from `main` before `main`'s `db66110` ("fix(ops): drop --no-cpu-throttling, redesign sweeper scope") landed; this branch independently made the same `--no-cpu-throttling` removal (see "Interim action taken" above) on its own fork of `scripts/gcp-bootstrap.ps1`, so this ADR's own text and the deploy script briefly existed in two textually-independent forms — one on `main` (from `db66110`), one on this branch — describing the same functional outcome. Reconciled during the merge into `main` (2026-07-15): `db66110`'s content was a strict subset of this branch's later, fuller versions of both files, so the merge conflict resolved by keeping this branch's text throughout.

**Implemented by.**

- Task 1 (`ccf6b06`) — schema migration: `leaseOwner`/`leaseExpiresAt`/`deliveryAttempts` on `WorkflowExecution`, new `ExecutionEvent` and `RateLimitBucket` models.
- Task 2 (`4ec4243`) — Cloud Tasks config settings (`src/config.py`: queue, region, service account, `execution_lease_seconds`).
- Task 3 (`e5e382f`, `f786647`) — Postgres-backed atomic rate limiter (`src/security/rate_limit_pg.py`) + create-race fix.
- Task 4 (`aa08719`, `a6253da`) — wired the Postgres rate limiter into `app.state`; `RateLimiterProtocol` rename + call-site type cleanup.
- Task 5 (`3ed6bd9`) — persisted, sequence-numbered execution events (`src/engine/events_pg.py`'s `PostgresEventStore`, backed by the `execution_events` table).
- Task 6 (`2df586d`, `2351d18`, `1513e84`) — Postgres LISTEN/NOTIFY wake-up signal (`src/engine/events_notify.py`); connection-creation lock and shutdown-ordering fixes.
- Task 7 (`c27b78b`, `d7ac1ea`) — rewired `src/engine/events_wrapper.py`/`LangGraphExecutor` to emit through `PostgresEventStore` + NOTIFY; isolated event-emit failures from execution state.
- Task 8 (`eff0719`, `8d1d1ac`) — WebSocket reconnect cursor + LISTEN-based live delivery (`src/api/events_ws.py`); hardening (poll-on-timeout, param validation, dead-client detection).
- Task 9 (`8b0f373`, `a043930`, `7ab3b29`) — Cloud Tasks enqueue wrapper (`src/execution/cloud_tasks.py`); cached client + shutdown wiring; concurrent-callers test-rigor fix.
- Task 10 (`e7e736a`, `b40261a`; fast-follow noted in `dac3099`) — `POST /internal/claim-and-run` (`src/api/internal.py`), claiming via `SELECT ... FOR UPDATE SKIP LOCKED`; claim-query status filter + transaction-timeout scale fix.
- Task 11 (`c41a752`, `96fea2a`; fast-follow noted in `057b660`) — `sweep_expired_leases`/`sweep_old_execution_events` + `POST /internal/sweep` (Cloud Scheduler target, replacing the in-process sweeper loop per the 2026-07-14 addendum above); lease-clear ordering + sweep-isolation fixes.
- Task 12 (`afd7b30`, `b54ee00`, `226fc0a`; fast-follow noted in `5453b01`) — `POST /executions` routed through Cloud Tasks instead of `BackgroundTasks`; queued-execution delete/cancel/stuck-enqueue gap fixes; unified claim-query status list + enqueue-failure write guard.
- Task 13 (`e50cb81`) — approval resume (in-app + email-link) and external-invoke (`POST /api/run/{slug}`) routed through Cloud Tasks, same mechanism as Task 12.
- Additional claim-and-run hardening (`4674ddc`, `fdd97c4`) — restored fast-fail crash handling in `claim_and_run` alongside the lease/sweep recovery path; guarded the fast-fail write against a concurrent cancel and against leaking raw exception text.
- Task 14 (`9ea55a2`, `cb5ddd6`) — Cloud Tasks queue + OIDC service-account provisioning (`scripts/gcp-bootstrap.ps1`); runtime service-account enqueue-permission fix. Originally diverged from `main`'s independent `db66110` fix to the same file; reconciled at merge — see "Main/branch divergence — resolved at merge" above.
- Task 15 (`a0bab20`, `aa458d0`; fast-follow noted in `cc9ab19`) — real-Postgres integration tests for lease recovery + concurrent claims; caught and fixed the `lease_expires_at` timestamptz-cast production bug described in the completion-summary addendum above.
- Task 16 (`136e168`) — ADR status/completion summary, `docs/architecture.md`, `docs/deferred-backlog.md`, `CHANGELOG.md`.
- Final holistic branch review (`e270c542d92e517e2ad6e452e37e6328befac046`, plus pyright cleanup `f5b6fc7`) — found and fixed the stale-lease resume bug described in the completion-summary addendum above; brought the full-repo `pyright src tests` scan to 0 errors (three issues invisible to the narrower per-task scans used throughout the branch).

All commits originated on branch `feat/durable-execution-cloud-tasks` (forked from `main` at `9f1b131`) and merged into `main` on 2026-07-15.

**Related.** `docs/claude-improvement-backlog.md` §P1-2 and §P1-4; `docs/deferred-backlog.md` (P1-2/P1-4 entries); ADR-0031 (the row-locking POC this reuses); `scripts/poc_persistence_row_lock.py`; `src/maintenance/execution_sweeper.py` (the existing sweep pattern this extends or replaces); `scripts/gcp-bootstrap.ps1` (the deployment config this analysis is grounded in); `docs/superpowers/plans/2026-07-13-durable-execution-cloud-tasks.md` (implementation plan, Task 11/14 updated per the 2026-07-14 addendum, all 16 tasks complete per the 2026-07-15 addendum).
