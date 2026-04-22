# Changelog

All notable changes to Composer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Phase 6e — Vector-DB (2026-04-21)

#### Added
- [Phase 6e design spec](docs/superpowers/specs/2026-04-21-phase-6e-vector-db-design.md) + ADR-0020.
- `src/vectordb/` module — new top-level package.
  - `src/vectordb/embedding.py` — `embed_text_openai()` via the OpenAI embeddings API. `text-embedding-3-*` models accept `dimensions` param; older models omit it.
  - `src/vectordb/providers/` — one file per provider, each exporting `async def query(embedding, config) -> list[VectorDbResult]`. Shared `QueryConfig` + `VectorDbResult` frozen dataclasses in `base.py`.
  - **5 providers:** Pinecone (REST `/query`), Qdrant (REST `/collections/{name}/points/search`), Chroma (REST batch-indexed with `1 - distance` score), Weaviate (GraphQL with `_additional {id,distance,vector}` + `1 - distance` score), Milvus (REST `/v1/vector/search`; DSL-only filter — dict filters log warn + skip).
- `src/executors/vector_db.py` — orchestrator. Substitutes config fields, embeds via OpenAI, dispatches via `_PROVIDERS` dict map, applies score-threshold filter, optionally joins results with separator/prefix/suffix + `{{index}}` placeholder. Dual output: `variables[output_variable]` + `variables.lastOutput` (joined text if `joinResults=True`, else the full output dict).
- `src/engine/workflow.py` — `VectorDbNodeData` tightened with 18+ explicit fields matching OAB `types.ts` + executor reads. `VectorDbProvider` + `EmbeddingProvider` Literal aliases exported.
- Unit tests: ~30 across `tests/unit/vectordb/` + `tests/unit/executors/test_vector_db.py` (6 embedding + 4 Pydantic + 3-4 per provider × 5 + 14 executor with 5-way parametric dispatch).

#### Changed
- Non-OpenAI embedding providers (`cohere`/`jina`/`pinecone-inference`) raise `NotImplementedError` until a later phase.
- Stale "unshipped Phase 6 sentinel" tests removed — Phase 6 closes the executor catalog, all 18 node types now have registered executors: `test_unshipped_type_raises_with_phase_hint` (registry) and `test_build_graph_rejects_unshipped_executor_type` (graph_builder) deleted. `test_run_marks_failed_on_exception` (langgraph_executor) migrated to monkeypatch `build_graph` → raise, preserving the behavioral contract.

#### Notes
- **Phase 6 is now complete.** All 18 node types in OAB's catalog have Composer executors:
  - Phase 1: start, end
  - Phase 2: agent
  - Phase 3a/3b: mcp
  - Phase 4a/4b: http, transform, data-transform, extract, set-state, if-else, while
  - Phase 5a: user-approval
  - Phase 6a-e: note (skip), join-chunks, guardrails, gamma-ai, arcade, vector-db
- No automated integration tests for vector-db: each provider requires real credentials + a populated index. Unit tests via `pytest-httpx` pin the wire shapes for all 5 providers.
- Per-node API keys (`vectorDbApiKey`) use `{{...}}` substitution — users parameterize via state/env.
- `OPENAI_API_KEY` is the only global (for embeddings); per-provider keys live on the node.

#### Verified
- 509/509 unit tests green (+41 from Phase 6d 468 — net after 2 stale sentinel deletions).
- Pyright 0 errors, ruff + format clean.

### Phase 6d — Arcade (2026-04-21)

#### Added
- [Phase 6d design spec](docs/superpowers/specs/2026-04-21-phase-6d-arcade-design.md) + ADR-0019.
- `src/executors/arcade.py` — `ArcadeExecutor` calls arcade.dev's tool-execution API via direct HTTP (no SDK). Two-step protocol: `POST /v1/tools/authorize` → `POST /v1/tools/execute`. If auth is pending, pauses via Phase 5a's `interrupt()` primitive; user completes OAuth externally and calls `POST /executions/{id}/resume` with `decision='approved'` (retry) or `'rejected'` (`ArcadeUserCanceledError`). Retry counter `_arcade_retries_<node_id>` caps resume attempts at `MAX_RETRIES=3` to prevent infinite loops on broken OAuth URLs.
- `src/engine/workflow.py` — `ArcadeNodeData` tightened: `tool` (required, alias `arcadeTool`), `input` (default `{}`, alias `arcadeInput`), `user_id` (default `'workflow-builder'`, alias `arcadeUserId`).
- `src/config.py` — `arcade_api_key` setting (env var `ARCADE_API_KEY`).
- SSE `approval-pending` event payload extended with optional `auth_url` / `auth_id` / `tool_name` fields (back-compat: existing user-approval consumers ignore unknown keys).
- Exception classes: `ArcadeNodeError` (config / network), `ArcadeAuthError` (auth failed or retry limit), `ArcadeUserCanceledError` (user rejected at resume).
- Unit tests: 14 via `pytest-httpx`; `interrupt()` mocked to raise `GraphInterrupt` in the pending-auth case. Covers happy path, all three output-extraction branches, auth-pending with state mutation assertion, auth-failed, resume approved/rejected paths, retry-limit exceeded, variable substitution, missing key, network error, 4xx, missing id, registry.

#### Changed
- `/executions/{id}/resume` now serves TWO interrupt sources: user-approval (Phase 5a) AND arcade-auth (Phase 6d). Same endpoint, same event type, same retry primitive. Executor owns the re-entry semantic (ADR-0019).

#### Notes
- Max runtime unbounded (depends on user completing OAuth). Retry counter caps the number of resume cycles.
- Variable substitution applies to each string value in `arcadeInput`; non-string values pass through unchanged.
- Output extraction: `result.output.value` first, then `result.output`, then the whole `result` dict — matches OAB's fallback chain.
- Retry counter persistence: executor mutates `state["variables"]["_arcade_retries_<node_id>"]` BEFORE calling `interrupt()`. LangGraph's Pregel captures pre-interrupt state in the checkpoint, so the counter survives through the pause.
- **Integration test is manual-only.** Arcade's OAuth flow can't be automated in CI. Smoke-test pattern documented in spec §7.2.

#### Verified
- 468/468 unit tests green (+18 from Phase 6c 450: 4 Pydantic tests + 14 executor tests).
- Pyright 0 errors, ruff + format clean.

### Phase 6c — Gamma-AI (2026-04-21)

#### Added
- [Phase 6c design spec](docs/superpowers/specs/2026-04-21-phase-6c-gamma-ai-design.md).
- `src/executors/gamma_ai.py` — `GammaAiExecutor` calls the Gamma.app public API (`https://public-api.gamma.app/v1.0`) to generate a presentation/document/webpage. Two-step protocol: POST create → poll GET until `state=completed` or `failed`. OAB-compatible cadence: 60s initial wait, 10s interval, 4-min max. When `exportAs` in {pptx, pdf}, waits up to 60s more for the download URL. `lastOutput = downloadUrl || gammaUrl`. Module-level sleep constants (`INITIAL_WAIT_SECONDS`, `POLL_INTERVAL_SECONDS`, `MAX_POLL_SECONDS`, `EXPORT_WAIT_SECONDS`, `EXPORT_POLL_INTERVAL`) for test monkeypatching.
- `src/engine/workflow.py` — `GammaAiNodeData` tightened with 8 explicit fields matching OAB `types.ts:77-85` (`prompt`, `format`, `textMode`, `numCards`, `textAmount`, `imageSource`, `language`, `exportAs`). Enum literals pinned (`presentation`/`document`/`social`, `generate`/`condense`/`preserve`, `brief`/`medium`/`detailed`, `pptx`/`pdf`/`web`).
- `src/config.py` — `gamma_api_key` setting (env var `GAMMA_API_KEY`).
- Unit tests: 13 via `pytest-httpx`. Sleep monkeypatched so tests run fast.

#### Notes
- Phase 6 was split — the originally planned 6c (Gamma-AI + Arcade) is now **6c (Gamma-AI only)** + **6d (Arcade)** + **6e (Vector-DB)**. Arcade's auth-interrupt flow (reuse of Phase 5a's `/resume` pattern) warrants its own focused sub-phase.
- Max runtime ~6 minutes. Runs inline in the BackgroundTask (machine-bounded wait; no LangGraph interrupt needed).
- Transient 4xx/5xx during polling is retried; POST create failure or `state=failed` raises `GammaNodeError`. Polling timeout (5 min wall clock) returns last-known status without raising — OAB-compatible.
- **Integration smoke test skipped in Phase 6c.** `GAMMA_API_KEY` is present in `.env`, but a real generation takes ~5 minutes and consumes API credits; can be run manually when needed.

#### Verified
- 450/450 unit tests green (+17 from Phase 6b 433: 4 Pydantic tests + 13 executor tests).
- Pyright 0 errors, ruff + format clean.

### Phase 6b — Guardrails (2026-04-21)

#### Added
- [Phase 6b design spec](docs/superpowers/specs/2026-04-21-phase-6b-guardrails-design.md) + ADR-0018.
- `src/executors/guardrails.py` — `GuardrailsExecutor` calls the Phase 2 LLM provider framework as a safety classifier. Four checks (PII, moderation, jailbreak, hallucination), any subset enabled per node. Concurrent via `asyncio.gather`. Prompts frozen in source (spec §5). Response parsing: `first_word.upper().startswith("YES")`; ambiguous non-YES treated as NO (anti-false-positive bias).
- `src/engine/workflow.py` — `GuardrailsNodeData` tightened with explicit fields matching OAB `types.ts:86-106` (`piiEnabled`, `moderationEnabled`, `jailbreakEnabled`, `hallucinationEnabled`, `actionOnViolation`, `model`).
- Output convention: `_guardrails_result = {passed, checks_run, violations, message}` (for `if-else` branching) + `lastOutput` = human-readable summary.
- `GuardrailsNodeError` wraps LLM failures; `GuardrailViolationError` fires when `action_on_violation='block'` + any violation → execution `failed`.
- Integration test against real Anthropic (Claude Haiku) + real Neon: PII detection path.

#### Changed
- `action_on_violation='block'` → `GuardrailViolationError` → execution status `failed`. `'warn'` → pass-through with violations populated.
- Sentinel tests migrate: `guardrails`/`Phase 6` → `vector-db`/`Phase 6` across three test files (`test_registry`, `test_graph_builder`, `test_langgraph_executor`). Same precedent as Phase 5a's `user-approval` → `guardrails` migration.

#### Notes
- OAB shipped a placeholder (`lib/workflow/executors/tools.ts:80` — 4-word hardcoded bad-word list with `TODO: Integrate with content moderation APIs`). Composer's executor supersedes it with real LLM-based classification while matching OAB's output shape.
- Model falls back to `DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"` when not set on the node (matches `src/executors/agent.py`'s pattern). Users can pin a cheap/fast model when guardrails run hot.
- Prompts are NOT user-configurable — guardrails is a safety feature, deterministic + auditable. Users wanting custom rules should compose `agent` + `if-else`.

#### Verified
- 433/433 unit tests green (+16 from Phase 6a 417: 4 Pydantic tests + 12 executor tests).
- 1/1 integration test green against real Anthropic + real Neon on first try (no fix-ups needed).
- Pyright 0 errors, ruff + format clean.

### Phase 6a — Note + Join-Chunks (2026-04-21)

#### Added
- [Phase 6a design spec](docs/superpowers/specs/2026-04-21-phase-6a-note-join-chunks-design.md).
- `src/executors/join_chunks.py` — `JoinChunksExecutor` concatenates a list of chunks (strings, or dicts with `content`/`metadata`) with configurable separator/prefix/suffix; optionally appends `[metadata: {json}]` per chunk. Output to `lastOutput`. Errors: missing variable / non-list value → `JoinChunksNodeError`; empty list → empty string.
- `src/engine/workflow.py` — `JoinChunksNodeData` tightened with explicit fields (replaces Phase 1 `config: dict[str, Any]` placeholder). Matches OAB `types.ts:132-137` aliases (`joinChunksVariable`, `joinChunksSeparator`, `joinChunksPrefix`, `joinChunksSuffix`, `joinChunksIncludeMetadata`).
- Integration test against real Neon: `start → set-state(list) → join-chunks → end`, asserts final `lastOutput`.

#### Notes
- OAB did NOT ship an executor for `join-chunks` (the type was declared in `types.ts` but unwired in `langgraph.ts`). Composer implements the behavior per the declared field semantics.
- `note` is visual-only; already skipped at graph-build time since Phase 1. The existing regression test at `tests/unit/engine/test_graph_builder.py:166` locks this behavior (inspects the compiled graph to confirm note nodes are NOT registered as LangGraph nodes).
- Phase 6a is the "local executor" sub-phase; 6b (guardrails), 6c (gamma-ai + arcade), and 6d (vector-db) add external-API integrations next.

#### Verified
- 417/417 unit tests green (+16 from Phase 5b baseline of 401: 12 executor tests + 4 Pydantic model tests).
- 1/1 integration test green against real Neon.
- Pyright 0 errors, ruff + format clean.

### Phase 5b — SSE streaming (2026-04-21)

#### Added
- [Phase 5b design spec](docs/superpowers/specs/2026-04-21-phase-5b-sse-streaming-design.md) + ADR-0017.
- `src/engine/events.py` — `ExecutionEventBus` (in-process asyncio fanout, bounded per-subscriber queue with drop-oldest overflow) + `ExecutionEvent` frozen dataclass with typed `EventType` literal.
- `src/engine/events_wrapper.py` — `wrap_executor_with_events` applied inside `graph_builder.build_graph`. Every executor emits `node-start` / `node-complete` automatically (no per-executor changes).
- `src/engine/context.py` — new ContextVars `current_execution_id`, `current_event_bus`. Set by `LangGraphExecutor._prepare_compiled`; read by the node wrapper.
- `src/engine/langgraph_executor.py` — emits `status-change(running/completed/failed/waiting_approval)` on every transition, `approval-pending` on pause; closes the event stream on every terminal path. `__init__` gains optional `event_bus` parameter (default None for back-compat).
- `src/api/events.py` — `GET /executions/{id}/events` Server-Sent Events endpoint. Snapshot-on-subscribe (first frame is `status-change` with current DB status), 15s keepalive via SSE comment, auto-unsubscribe on client disconnect, bounded per-subscriber queue.
- `src/api/executions.py` — `POST /resume` now emits `approval-resumed` with `{node_id, decision}` synchronously (before the BackgroundTask).
- `src/storage/db.py` — `event_bus` attached to `app.state` in `prisma_lifespan` alongside the checkpointer; new `get_event_bus(request)` dependency.
- Integration test: full approved-path SSE stream against real Neon; asserts `approval-resumed`, `status-change(completed)`, and post-resume `node-start`/`node-complete` events.

#### Changed
- `LangGraphExecutor.__init__` accepts optional `event_bus` parameter (defaults to None for back-compat with existing tests).
- All executors participate in event emission via the graph-build-time wrapper — no per-executor edits needed.

#### Fixed
- Integration-test race caught by real Neon: `approval-pending` emits before the SSE subscriber's HTTP connection can establish. Snapshot-on-subscribe (spec §9) does not replay past events, so asserting on `approval-pending` in the integration test was racy. Revised assertions rely only on events guaranteed after the subscriber connects: `approval-resumed`, `status-change(completed)`, and post-resume node events. Commit `a6a4a98`.

#### Verified
- 401/401 unit tests green (+19 from Phase 5a baseline of 382).
- 3/3 integration tests green against real Neon: SSE approved path + approved + rejected (Phase 5a regression).
- Pyright 0 errors, ruff + format clean.

#### Deliberate design choices (see ADR-0017)
- In-process asyncio bus, not Postgres `LISTEN/NOTIFY` or Redis. Zero new infra; multi-worker fanout is a Phase 9 concern.
- SSE over WebSocket — stateless, plays with standard HTTP middleware, no new dependencies.
- Snapshot-on-subscribe: first frame is always `status-change` with current DB status. No event replay; DB remains authoritative for history via `GET /executions/{id}`.
- Bounded per-subscriber queue (128) with drop-oldest overflow: slow subscribers never stall the emitter. Stream is advisory; authoritative state lives in Postgres.
- Five event types only. LLM token streaming is a Phase 10 UI concern.
- Any authenticated user can subscribe in 5b (same as `/resume`). RBAC is Phase 7b+.

### Phase 5a — User-approval + interrupt/resume (2026-04-21)

#### Added
- [Phase 5a design spec](docs/superpowers/specs/2026-04-21-phase-5a-user-approval-design.md) + ADR-0016.
- Prisma `Approval` table + `ApprovalDecision` enum (per-decision audit row; cascade-deletes with execution; indexed by `executionId` and `approverUserId`).
- `src/executors/user_approval.py` — `UserApprovalExecutor` calls LangGraph's `interrupt({node_id, prompt})` (reads `data.approval_message` alias `approvalMessage` for the prompt template); records `_approval_<node_id>` in variables on resume; `UserApprovalNodeError` for invalid decisions.
- `src/engine/graph_builder.py` — extends Phase 4b's conditional-edges pass with `user-approval` routing (branches `{approved, rejected}`); `_route_user_approval` reads `_approval_<node_id>` from variables.
- `src/engine/langgraph_executor.py` — detects pause via `compiled.aget_state(config).next` after `ainvoke` (see Fixed below); merges `_pending_approval_node` / `_pending_approval_prompt` into `variables`; new `resume(execution_id, decision)` method calls `compiled.ainvoke(Command(resume=decision))`; chained pauses re-mark `waiting_approval`.
- `POST /executions/{id}/resume` — accepts `{decision: 'approved'|'rejected', note?: str}`; 404 / 409 / 422 / 500 error paths; writes Approval row; flips status to `running` before scheduling the `BackgroundTasks` resume.
- Integration tests: approved path + rejected path (real Neon); assert `Approval` row contents + final variable state.

#### Changed
- Status transitions: `running → waiting_approval → running → completed|failed`. No new terminal status — the rejected branch completes normally.
- Sentinel tests migrate: `user-approval`/`Phase 5` → `guardrails`/`Phase 6` across three test files (graph_builder, langgraph_executor, executors/test_registry).
- `LangGraphExecutor` refactored: `_prepare_compiled` / `_mark_completed` / `_mark_waiting_approval` / `_mark_failed` / `_load_execution` helpers factor the shared prep + DB-update logic between `run()` and `resume()`.

#### Fixed
- One bug caught by real-Neon integration testing: LangGraph's `interrupt()` in current versions does NOT propagate `GraphInterrupt` out of `ainvoke` — the Pregel runtime catches it internally, persists the checkpoint, and returns cleanly. Our original `except GraphInterrupt` was dead code; every execution would have completed immediately, skipping the pause. Fix: inspect `compiled.aget_state(config).next` after `ainvoke`; extract pending interrupt payload from `snapshot.tasks[*].interrupts[*].value`. Same check applied to `resume()` for chained pauses. Commit `b07d8de`.

#### Verified
- 382/382 unit tests green (+18 from Phase 7a baseline of 364).
- 2/2 Phase 5a integration tests green against real Neon (approved + rejected paths).
- Prior regression tests unaffected (sentinel tests migrated cleanly; Phase 4b conditional-edges untouched).

#### Deliberate design choices (see ADR-0016)
- Checkpoint + resume (LangGraph `interrupt()` + `aget_state` pause detection), not blocking request or re-run-from-scratch. Pins no API workers; preserves side-effect correctness.
- `Approval` as a dedicated Prisma table, not inline JSON on `WorkflowExecution.variables`. Enables audit trail + future multi-approver workflows.
- Status flip to `running` happens BEFORE the `BackgroundTask`, so polling clients never observe stale `waiting_approval` while the resume is in flight.
- Rejected branch is ordinary graph routing — no special `"rejected"` top-level status. The decision lives on the `Approval` table.
- Any authenticated user may resume in 5a (dev-mode fallback returns `'dev'` per ADR-0015). RBAC (only the workflow creator / tagged approvers can resume) is Phase 7b+.

### Phase 7a — Deployment-mode toggle + auth middleware (2026-04-21)

Brought forward from Phase 7 because user-approval (Phase 5) needs authenticated user context. Phase 5 (user-approval + SSE) is re-sequenced to run next.

#### Added
- [Phase 7a design spec](docs/superpowers/specs/2026-04-21-phase-7a-deployment-mode-design.md) + ADR-0014 + ADR-0015.
- Prisma `User` table + `UserRole` enum. Populated only in standalone deployments; embedded deployments leave it empty (userId strings come from IEP-signed JWT `sub` claims).
- `src/config.py` — `deployment_mode`, `iep_jwt_issuer`, `iep_jwks_url`, `iep_shared_secret`, `iep_ui_origin`, `bcrypt_rounds`.
- `src/security/auth.py` — `AuthError` + `get_current_user_id` FastAPI dependency with mode-aware dispatch (HS256 w/ Composer's `JWT_SECRET` in standalone vs HS256 w/ `IEP_SHARED_SECRET` in embedded) + dev-mode fallback (ADR-0015).
- `src/security/passwords.py` — bcrypt hash + verify helpers (cost factor from `bcrypt_rounds`).
- `src/api/auth_standalone.py` — `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/disconnect` (registered only in standalone).
- `src/api/auth_common.py` — `/auth/me` (registered in both modes; standalone returns User row, embedded returns JWT claims).
- Deps: `bcrypt>=4.0.0`, `email-validator>=2.0.0` (for Pydantic `EmailStr`).
- 7 new integration tests: standalone register→login→me→refresh lifecycle, duplicate-email 409, missing-auth probe; embedded correct-issuer 200, wrong-issuer 401, missing-header 401, bad-signature 401 — all pass against real Neon.

#### Changed
- `src/main.py` — mode-aware router registration + CORS allowlist + startup validation. Embedded mode with missing `IEP_JWT_ISSUER` or both of `IEP_SHARED_SECRET`/`IEP_JWKS_URL` refuses to boot with a clear RuntimeError.
- `src/engine/state.py` — `WorkflowStateDict` gains `user_id: NotRequired[str]`.
- `src/engine/langgraph_executor.py` — propagates `execution.userId` into initial state before `compiled.ainvoke`.
- Every route handler and executor that hardcoded `user_id="dev"` now declares `Depends(get_current_user_id)` or reads `state["user_id"]`. Only remaining `"dev"` literal in `src/` is the single ADR-0015 fallback inside `src/security/auth.py`.
- Phase 5 (user-approval + SSE) is re-sequenced to run after 7a.

#### Fixed
- One integration-test bug caught by running against real Neon: `@composer.test` emails rejected by `email-validator` (reserved TLD per IANA). Changed to `@example.com` (RFC 2606 reserved-for-examples).

#### Verified
- 364/364 unit tests green (+48 from Phase 4b baseline of 316).
- 7/7 Phase 7a integration tests green against real Neon.
- 6/6 regression check: prior Phase 1/4a/4b integration tests (start→end, if-else, while, HTTP→Extract→Set-State) continue to pass via ADR-0015 dev-mode fallback.

#### Deliberate design choices (see ADR-0014 + ADR-0015)
- Deployment mode read from env var at startup; no live-switch. Auth semantics differ too fundamentally between modes for live-flipping to be safe.
- Dev-mode fallback (`user_id="dev"` when no Authorization + `environment=development`) keeps existing integration tests working without JWT refactor. Production deploys (`ENVIRONMENT=production`) get no fallback; startup logs prominent WARN when fallback is active.
- 7a ships HS256 shared-secret path for IEP JWT verification; JWKS/RS256 is Phase 7b.
- Onboarding UI (standalone-mode register/invite flows) activates when `deployment_mode=standalone` — documented for Phase 10.
- Login returns uniform 401 on unknown email OR wrong password (no enumeration side channel).

### Phase 4b — Control-flow executors (if-else, while) (2026-04-21)

**Phase 4 is now complete overall.** Combined with Phase 4a, Composer can express any OAB workflow topology except user-approval (Phase 5).

#### Added
- [Phase 4b design spec](docs/superpowers/specs/2026-04-21-phase-4b-control-flow-design.md) + ADR-0013.
- `WorkflowEdge.branch: str | None` field for conditional-edge labels (ADR-0013).
- `src/executors/if_else.py` — `IfElseExecutor` records the evaluated branch decision; actual routing done by the builder's closure.
- `src/executors/while_loop.py` — iteration-bounded loop with `max_iterations` cap (default 100); raises `WhileMaxIterationsError` on overflow. Per-loop iteration counter lives in `state.variables._while_iterations`.
- `graph_builder._branch_mapping` helper: validates edge-branch sets against required branches (exact match — no missing, no extras, no duplicates, no unknown names).
- `graph_builder._route_if_else` / `_route_while`: router closures that re-evaluate the condition fresh on each traversal.
- `graph_builder.build_graph` emits `add_conditional_edges` for if-else / while.
- Integration tests: two if-else routing workflows (true + false branch) + one while countdown — all run against real Neon without an LLM.
- Regression port: 4 OAB if-else behavioural-contract tests.

#### Changed
- `src/engine/workflow.py`: `WorkflowEdge` gains `branch: str | None = None`; `WhileNodeData` tightened (`condition: str | None = None`, `max_iterations: int = Field(default=100, alias="maxIterations")`).
- `src/engine/graph_builder.py`: `validate_workflow_shape` validates branch labels against source type; `build_graph` emits conditional edges after normal edges.
- `src/executors/_eval.py`: expanded scope with safe coercion functions (`int`, `float`, `str`, `bool`, `len`) so workflow authors can convert between types when template substitution stringifies values.
- Obsolete Phase-4 sentinel test `test_build_graph_rejects_conditional_edge_source` removed — if-else now ships; the `user-approval` sentinel test covers the unshipped-executor path.

#### Fixed
- Two bugs caught by running integration suite against real Neon (same verify-before-exit pattern as Phases 1, 2, 3a, 3b, 4a):
    - `_eval.py` was not catching `KeyError` / `IndexError` / `TypeError` / `ValueError` from simpleeval's subscript evaluator; they bubbled up past the `except` tuple. Now wrapped as `EvalError` uniformly.
    - `{{lastOutput}}` substitution stringifies int `lastOutput` into `"2"` when used in `set-state.stateValue`. Workflows that chain `set-state → while(variables['n'] > 0)` saw `TypeError: '>' not supported between instances of 'str' and 'int'`. Fixed by adding `int`/`float`/`str`/`bool` coercion functions to simpleeval's scope — workflow authors can now write `int(variables['n']) > 0` defensively. The substitution semantics themselves are unchanged (still returns str for single-template strings per Phase 2).

#### Deliberate design choices (see ADR-0013 + spec)
- Routing by edge label, not runtime inference from node-data fields. Single source of truth.
- Router closures evaluate condition fresh on each traversal (slight redundancy vs. reading from node_results, but deterministic; OAB does the same).
- No `break` / `continue` keywords in while body — chain an inner `if-else` routing to the exit target for early termination.

### Phase 4a — Linear Executors (http, set-state, transform, data-transform, extract) (2026-04-21)

#### Added
- [Phase 4a design spec](docs/superpowers/specs/2026-04-21-phase-4a-linear-executors-design.md) + ADR-0012.
- `src/variable_substitution.py substitute_in_value` — recursive `{{...}}` over dict/list/str (keys untouched, non-str passthrough).
- `src/executors/_eval.py` — `simpleeval` wrapper, the single eval primitive (ADR-0012). Scope: `variables`, `lastOutput`, `node_results`, per-call `extra_names`. No builtins, no imports, no dunder access.
- `src/executors/http.py` — HTTP request node (JSON/text response, dot-path extraction, template substitution on URL/headers/body, 60s timeout, non-2xx → `HttpNodeError`).
- `src/executors/set_state.py` — writes templated values into `state.variables[stateKey]` and aliases as `lastOutput`.
- `src/executors/transform.py` — single `simpleeval` expression over state.
- `src/executors/data_transform.py` — map / filter / reduce with per-item `simpleeval` evaluation; reduce additionally exposes `acc` in scope.
- `src/executors/extract.py` — LLM structured-output extraction reusing Phase 2's `structured_invoke` + Phase 3b's LangSmith threading.
- Integration test: `Start → HTTP → Extract → Set-State → End` against real Neon + real Anthropic + public jsonplaceholder endpoint.
- Regression test: 3 OAB HTTP-node behavioural contract tests (JSON parsing, non-2xx failure, URL templating).

#### Changed
- `src/engine/workflow.py`: `HttpNodeData` gains `responsePath`; `DataTransformNodeData` tightened from stub `config: dict` to proper fields (`operation`, `collection`, `expression`, `itemVar`, `initial`); `ExtractNodeData` gains `input_text` + `model`.
- `src/engine/graph_builder.py`: five new side-effect imports for the new executors.
- Sentinel tests move from `http`/`Phase 4` → `user-approval`/`Phase 5` across three files (shipped as part of the http executor commit).

#### Fixed
- Integration-suite bug caught by running against real Anthropic: LangChain's `convert_to_openai_function` (called by Anthropic's `with_structured_output`) requires a top-level `title` key in the JSON schema. Added `"title": "Todo"` to the Phase 4a integration test's extract schema. Not an executor bug — schema authoring pattern — but worth documenting so workflow authors know to include `title` on extract schemas.

#### Deliberate deviations from OAB
- `transform` / `data-transform` use `simpleeval` Python expression syntax, not OAB's JS `vm.runInNewContext`. No arrow functions, no template literals, no `?.`. `EvalError` names the failing expression + cause for mechanical translation.
- `http` does not follow OAB's retry/backoff config; Phase 9 (hardening) can introduce it.
- `extract` runs on Phase 2's `structured_invoke`; the LangSmith config threading (Phase 3b fix #6) applies automatically.

### Phase 3b — MCP OAuth + the six hard-won fixes (2026-04-21)

#### Added
- [Phase 3b design spec](docs/superpowers/specs/2026-04-20-phase-3b-mcp-oauth-design.md) + ADR-0011.
- Prisma `McpOAuthToken` + `McpOAuthState` tables (additive migration; `McpServer` unchanged).
- `src/mcp/oauth.py` — PKCE + state + resource derivation, `build_authorize_url`, `exchange_code_for_tokens`, `refresh_token`, `get_valid_access_token` (with service-account fallback for shared servers).
- `src/mcp/client.py` gains an optional async `auth_header_factory` hook — OAuth tokens refresh-on-use.
- `src/mcp/base.py` `McpToolProvider` OAuth branch: constructs a factory that calls `get_valid_access_token` per outbound request. Tokens never transit the client.
- REST endpoints: `POST /mcp-servers/{id}/oauth/authorize`, `GET /oauth/callback`, `POST /mcp-servers/{id}/oauth/disconnect`.
- `src/engine/context.py` gains `LangSmithConfig` + ContextVar; `LangGraphExecutor.run` sets it from Settings before `compiled.ainvoke`. `build_chat_model` wraps the returned model via `with_config` when tracing is enabled.
- Integration test against real Highspot MCP (seeded access token path — verifies server-side token retrieval + real MCP protocol end-to-end).
- Regression port: OAB's Highspot OAuth lifecycle spec — two tests asserting `resource` param on token exchange + refresh (the OAB multi-day debugging invariant).

#### Changed
- `McpToolProvider.__init__` now accepts `db: Any | None = None` + `user_id: str | None = None` as kwargs. Static-auth callers may omit both.
- `MCPClient.__init__` accepts `auth_header_factory` alongside the existing `auth_header` dict. Factory wins when both provided.
- `src/llm/providers.py build_chat_model` accepts `langsmith_config: LangSmithConfig | None`. When provided (and `tracing_v2` is True), wraps the model via `with_config`. Back-compat: `None` returns the raw model.
- `src/tools/base.py BuildContext` gains `langsmith_config: LangSmithConfig | None = None` field.
- `src/mcp/resolver.py` propagates `db + user_id` into `McpToolProvider` so the OAuth path's async factory can look up tokens.
- Phase 3a's sentinel test `test_provider_oauth_auth_not_implemented_in_3a` removed (OAuth has shipped).

#### Fixed
- OAB's six hard-won MCP fixes are now all in place in Composer: RFC 8707 `resource` on all four OAuth flows (3b, asserted on 4 separate unit tests + 2 OAB regressions), manual tool calling (3a), `inputSchema` camelCase (3a), server-side token retrieval (3b — tokens never transit the client), `isShared` + service-account token fallback (3a permissions + 3b OAuth token fallback), LangSmith config threaded (3b).
- Three Highspot-integration-test bugs caught by running against real Highspot + real Neon (same verify-before-exit pattern as Phases 1, 2, 3a): Prisma `Json?` wrapping on `.update()` (beyond the Phase 3a fix on `.create()`); `str.rstrip('/mcp')` strips char-set not suffix (replaced with `str.removesuffix`); `get_valid_access_token` treats `expiresAt=None` as never-expires (the integration test was simplified to seed a direct access token per user direction — real refresh coverage lives in unit + regression).

#### Verified
- All unit tests green: `ruff check`, `ruff format --check`, `pyright src tests` (strict), `pytest -m "not integration"` → 251 passed, 0 failed.
- OAB Highspot OAuth regression: 2/2 passing (both `resource` assertions green).
- Real-Highspot MCP stack: direct probe confirmed Composer's OAuth + MCP pipeline reaches Highspot's authentication boundary cleanly (JSON-RPC 401 "Invalid or expired token" — the token provided during verification had expired; stack itself is functionally correct).

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
