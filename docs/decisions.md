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
- **WebSocket deployment constraint** forces a container-based backend, documented in `docs/deployment/vercel-setup.md`.

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
