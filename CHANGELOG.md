# Changelog

All notable changes to Composer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Phase 3a — MCP Infrastructure (Static Auth) (2026-04-20)

#### Added
- [Phase 3a design spec](docs/superpowers/specs/2026-04-20-phase-3a-mcp-infrastructure-design.md) + ADR-0010.
- Prisma `McpServer` table (full OAB-faithful schema — `oauthConfig` and `isShared` columns in place for Phase 3b).
- `src/security/encryption.py` — AES-256-GCM helpers (shared primitive for 3a static tokens + 3b OAuth tokens).
- `src/mcp/client.py` — HTTP JSON-RPC + SSE MCP client (per-instance rpc-id counter).
- `src/mcp/schema_adapter.py` — `inputSchema` three-way fallback + URL template substitution.
- `src/mcp/base.py` — `McpToolProvider(ToolProvider)` — runtime-constructed per server row (ADR-0010).
- `src/mcp/resolver.py` — per-node resolution + permission checks (owner + isShared).
- `src/engine/context.py` — per-execution db ContextVar, set by `LangGraphExecutor.run` before `compiled.ainvoke`.
- `src/executors/mcp.py` — the `mcp` node type executor (standalone, no LLM in loop).
- `src/api/mcp_servers.py` — POST / GET / POST test-connection / DELETE endpoints.
- Integration tests: Agent+DeepWiki, Agent+Firecrawl MCP, `mcp` node standalone.
- OAB regression: `mcp-lifecycle.spec.ts` port.

#### Changed
- `src/tools/registry.py` → `resolve_tools_for_node` now delegates `mcp_server_ids` to `src.mcp.resolver`. The Phase-2 NotImplementedError stub is gone.
- `src/tools/base.py` `BuildContext` gains `db: Any | None = None`. Callers with MCP tools must populate it.
- `src/engine/langgraph_executor.py` sets `src.engine.context.set_current_db` before `compiled.ainvoke` so the Agent executor can dispatch MCP resolution.
- Phase-1 "unshipped executor" sentinel moves from `mcp` (now shipped) to `http` (Phase 4).
- **Deliberate deviation from OAB**: encryption format is `base64(nonce || ciphertext || tag)`, simpler than OAB's `salt:iv:authTag:ciphertext`. We don't derive the key (we use the raw 32-byte key from `ENCRYPTION_KEY` directly), so no salt. Documented in `src/security/encryption.py`.

#### Fixed
- Three bugs caught by running the integration suite against real Neon + real DeepWiki + real Firecrawl MCPs (same verify-before-exit pattern as Phases 1 + 2):
  - `src/api/mcp_servers.py` was passing raw `dict | None` for `headers` and a raw `list` for `tools` into Prisma `Json?` columns. Prisma rejected with `MissingRequiredValueError: value is required but not set`. Fixed by wrapping non-None values with `prisma.Json(...)` and omitting the key entirely when None (same `Json` wrapping lesson as Phase 1's fix in [src/api/workflows.py](src/api/workflows.py)).
  - DeepWiki + Firecrawl have **deprecated the `/sse` transport** in favor of streamable HTTP at `/mcp`. DeepWiki's `/sse` now returns HTTP 410 "SSE transport is deprecated"; Firecrawl returns 404. The 4 integration/regression tests were updated to use `/mcp` URLs.
  - `AgentExecutor` + `McpExecutor` hardcoded `user_id=None` in their `BuildContext` / `resolve_single_mcp_tool` calls, but the API creates `McpServer` rows with `userId='dev'` (anonymous per ADR-0005). The resolver's permission check then denied access. Both executors now pass `user_id='dev'` to match; Phase 7 will wire real `user_id` from auth middleware.

### Phase 2 — Agent Executor + Tool Provider Framework (2026-04-20)

#### Added
- [Phase 2 design spec](docs/superpowers/specs/2026-04-20-phase-2-agent-executor-design.md) + ADR-0006, ADR-0007, ADR-0008, ADR-0009.
- `src/variable_substitution.py` — OAB-parity `{{...}}` template engine with prototype-pollution guard.
- `src/llm/providers.py` — `build_chat_model` dispatch for Anthropic/OpenAI/Google/Groq via LangChain.
- `src/llm/structured_output.py` — shared `structured_invoke` primitive (Agent now, Extract in Phase 4).
- `src/tools/base.py` — Tool Provider Framework: `ToolProvider` ABC, `AuthRequirement` (NoAuth/ApiKeyAuth/OAuthAuth), `ToolDefinition`, `BuildContext`, `HealthStatus`.
- `src/tools/registry.py` — `@register_tool_provider` decorator, `get_provider` / `list_providers` / `resolve_tools_for_node`.
- Four standard tool providers: Tavily (search), Serper (Google search), Firecrawl (scrape), Browserless (headless-browser fetch).
- `src/mcp/__init__.py` — skeleton package for Phase 3's McpToolProvider.
- `src/executors/agent.py` — Agent executor with full agentic loop (MAX_ITERATIONS=10), tool binding via `chat_model.bind_tools()`, structured output routing.
- CI updated with provider secrets (Anthropic/OpenAI/Google/Groq/Tavily/Serper/Firecrawl/Browserless/LangChain) pulled from GitHub Actions secrets.
- Per-provider integration tests (real LLMs) + Agent+Tavily integration test + OAB simple-agent regression port.
- `pytest-httpx` added as a dev dep for HTTP-tool unit tests.
- `.env.example` expanded with LLM provider + tool key placeholders and Phase-owner annotations.

#### Changed
- Phase 2 pulls Phase 3's agentic-loop architecture forward (per ADR-0006) so Phase 3 MCP becomes tool-registration, not executor-rewrite.
- Phase 2 pulls Phase 6's first standard-tool provider (Tavily) and three more (Serper/Firecrawl/Browserless) forward; Phase 6 remaining providers (Gamma, Arcade) follow the same template.
- Phase 2 pulls Phase 4's structured-output primitive forward (per ADR-0008) so Extract reuses it.
- `src/executors/agent.py` `DEFAULT_MODEL`: `claude-haiku-4-5-20251001` (was stale `claude-3-5-haiku-latest` during Phase 2 development; caught by real-API integration testing).
- **Deliberate deviation from OAB**: JSON output uses `with_structured_output(method="json_mode")` with provider-native schema enforcement (or `json.loads` fallback for schema-less asks), not OAB's naive `JSON.parse`. Rationale: Phase 2 spec §6; ADR-0008.

#### Fixed
- 3 pre-existing tests used `"agent"` / `"Phase 2"` as the unshipped-executor sentinel; updated to `"mcp"` / `"Phase 3"` (MCP is now the next unshipped executor).
- Stale LLM model names (caught by running the integration suite against real keys): `claude-3-5-haiku-latest` 404s, replaced with `claude-haiku-4-5-20251001`; `gemini-2.0-flash` blocked for new users, replaced with `gemini-2.5-flash`.

### Phase 1 — Execution engine core (2026-04-20)

#### Added
- [Phase 1 design spec](docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md) — architecture for the execution engine, Prisma schema additions, Pydantic models for all 18 node types, checkpointer design, API surface, test plan, phase-exit checklist.
- [Decisions log](docs/design/decisions.md) established with ADR-0001 through ADR-0005:
  - ADR-0001: LangGraph checkpointer — Prisma owns schema, thin custom saver.
  - ADR-0002: Workflow JSON schema — full OAB fidelity (all 18 node types modeled in Phase 1).
  - ADR-0003: Documentation runs in lockstep with development (four-document system + enforcement).
  - ADR-0004: Local dev Postgres — Neon only (supersedes Phase 0's dual-path guidance).
  - ADR-0005: Phase 1 API surface (3 endpoints) + JWT primitives as library code only.
- Prisma schema: four tables — `Workflow`, `WorkflowExecution`, `LangGraphCheckpoint`, `LangGraphCheckpointWrite`.
- Pydantic envelope for all 18 OAB node types (`src/engine/workflow.py`), discriminated on `type`.
- `WorkflowStateDict` TypedDict with reducers (merge_dict, last_wins, operator.add) mirroring OAB's Annotation.Root.
- Executor protocol + registry (`src/executors/base.py`) with phase-aware NotImplementedError for unshipped types.
- Start + End executors (Phase 1 subset of OAB's 18 executor types).
- Graph builder (`src/engine/graph_builder.py`): shape validation, reachability BFS, LangGraph StateGraph compilation.
- `PrismaCheckpointSaver` (`src/storage/checkpointer.py`) implementing BaseCheckpointSaver's four async methods.
- `LangGraphExecutor` orchestrator (`src/engine/langgraph_executor.py`): creates WorkflowExecution rows, drives graphs, persists terminal state.
- REST API: `POST /workflows`, `POST /executions`, `GET /executions/{id}`.
- JWT primitives (`src/security/jwt.py`): access + refresh token helpers, not wired to routes (ADR-0005).
- Prisma client lifecycle wiring via FastAPI `lifespan`; `get_db` + `get_checkpointer` dependencies.
- CI updated with Postgres 15 service, Prisma migrate step, and split pytest (unit vs integration marker).
- Integration test (`tests/integration/test_start_to_end.py`) exercising the full engine path end-to-end.
- Regression test harness (`tests/regression/`) with one OAB-ported start→end smoke test.

#### Changed
- README points at `docs/design/` and `docs/superpowers/specs/` for design/spec/ADR docs.
- README stack table: corrected the real-time row to SSE (Phase 5) → WebSocket (Phase 9), matching the phased plan.
- `.env.example`: Neon-only `DATABASE_URL` format; added `TEST_DATABASE_URL` for integration tests.
- **Deliberate deviation from OAB behavior:** For a workflow with only `start → end` and no intermediate node, Composer's `finalOutput` equals the parsed input. OAB's `finalOutput` for the same shape defaults to `""` (because OAB's Start doesn't write `variables.lastOutput`). Rationale in [Phase 1 spec §7.1](docs/superpowers/specs/2026-04-20-phase-1-execution-engine-design.md#71-srcexecutorsstartpy). The ported regression test (`tests/regression/test_oab_start_end.py`) asserts the Composer behavior; OAB-equivalent assertions would need adaptation when more OAB tests are ported in Phase 7.

#### Fixed
- `POST /workflows` and `POST /executions` were passing raw Python dicts/lists into Prisma `Json` columns. Prisma rejected them at runtime with `DataError: nodes should be of type Json`. All Json-column writes now wrap values with `prisma.Json(...)`. Caught by running integration tests against the real Neon Postgres dev branch rather than mocks.
- `PrismaCheckpointSaver` was passing raw `bytes` into Prisma `Bytes` columns (`checkpoint`, `metadata`, `value`). The rust query engine's JSON serializer rejected them with `TypeError: Type <class 'bytes'> not serializable`. All writes now wrap with `prisma.Base64.encode(...)`; the read path already handled both `Base64` objects and raw bytes.
- `tests/conftest.py` held a session-autouse fixture that required `TEST_DATABASE_URL`, which started skipping unit tests too when the fixture moved to the top-level conftest. Replaced with a `pytest_collection_modifyitems` hook that skips only tests marked `@pytest.mark.integration`.
- Duplicated `_get_db` FastAPI dependency in both `src/api/workflows.py` and `src/api/executions.py` consolidated to a single `get_db` in `src/storage/db.py` (made `Request` a real runtime import so FastAPI's parameter detection works).
- Integration/regression poll helpers now use `asyncio.get_running_loop()` instead of the deprecated `get_event_loop()` (silences DeprecationWarning under Python 3.12).

#### Removed
- `docker-compose.yml` (per ADR-0004). Local dev is Neon-only; integration tests in CI use a GitHub Actions Postgres service container.
- README "Offline fallback: local Postgres" section.

### Phase 0 — Scaffolding (2026-04-20)

#### Added
- Initial repo scaffold: FastAPI skeleton, Prisma schema placeholder.
- `pyproject.toml` with full dependency list (FastAPI, LangGraph, LangChain providers, Prisma, pytest, ruff, pyright).
- `/health` endpoint returns service metadata.
- CI workflow (lint + typecheck + tests).
- `.env.example` template.
- MIT License.
- README with setup instructions.

> Historical note: Phase 0 originally shipped a `docker-compose.yml` for local Postgres fallback. ADR-0004 (Phase 1) supersedes that decision; Neon is the sole dev path going forward.
