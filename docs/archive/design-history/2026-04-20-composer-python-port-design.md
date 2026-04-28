# Composer — Python Rebuild Design

**Date:** 2026-04-20
**Author:** Balaji Rajan, with AI-assisted brainstorming
**Status:** Design approved; implementation plan to follow
**Supersedes (in part):** `2026-04-15-composer-03-engineering-blueprint.md` — this design replaces the "module-inside-IE-monorepo" plan with a "separate repo, IE-compatible stack" plan

---

## 1. Context and Strategic Frame

On 2026-04-15 we produced six documents (`composer-01` through `composer-06` in this directory) proposing Composer as a new module inside the IE monorepo: a Python-ported Open Agent Builder that lets tenant admins and business users compose workflows over IE's agent catalog.

David Lawton (IE owner) responded with a critical analysis on 2026-04-20 (`IE Investigation/OAB Composer - Critical Analysis.docx`). His position: the strategic direction is sound, but timing and sequencing are wrong. He recommends:

1. **Now:** Ship IE's MCP adapter (~4 weeks) — already scoped and security-reviewed, benefits six consumer categories (IDEs, CI/CD, OAB, etc.)
2. **Next:** Use OAB standalone with MCP bridge (~2–4 weeks) to pilot with consultants + one tenant, collect real usage data
3. **Then:** Validate Composer thesis with actual usage data
4. **Then:** If validated, port Composer into IE with confidence

His concrete concerns:
- The Python port is R&D, not disciplined re-implementation (LangGraph JS→Python dynamic graph compilation has no existing pattern in IE)
- Security model for user-composed workflows is underspecified (prompt injection, data exfiltration via chained MCP calls, resource exhaustion)
- MCP adapter benefits 6 consumer categories; Composer benefits 1 (non-developer workflow builders)

### Our response: a clean-break rebuild on IE's stack

We are not replying to David with a counter-position. We use the window his sequencing implicitly grants us — the ~4 weeks while he ships the MCP adapter, extended to ~13 weeks total (10 backend + 3 UI) — to proactively rebuild OAB on IE's Python stack.

**Net effect:**
- David's stack concern (#1) is addressed preemptively: when the Composer conversation resumes, the Python rebuild already exists on IE-compatible infrastructure
- David's security concern (#2) is outside this design's scope and will be addressed separately when Composer-in-IE becomes a real proposal
- David's sequencing (#3) is implicitly accepted: MCP adapter ships first, Composer pilots with MCP bridge
- We retain full control of OAB's evolution; no dependency on IE's decisions during the rebuild

### The four governing rules

This rebuild is governed by four rules set by the project owner:

1. **Migration plan.** A phased plan to rebuild OAB on IE's Python stack.
2. **OAB continues as-is.** No changes. No fixes. No commits. The OAB repo becomes read-only reference material.
3. **Build everything fresh using OAB as lookup.** Composer is written idiomatically in Python, not translated line-by-line. OAB's source is read-only behavioral reference — nothing in Composer imports, forks, or depends on OAB code.
4. **Composer passes OAB's regression suite for completion.** Objective behavioral parity; internal structure is free to be cleaner than OAB's.

### Mode shift: frozen reference, not parallel maintenance

An earlier draft of this design treated the Python work as a "port" running alongside an evolving TS OAB with "critical bugs only" maintenance. This version is stricter and simpler:

**TS OAB is frozen. Composer is the new codebase. There is no parallel maintenance.**

- TS OAB: no commits whatsoever for the duration of the rebuild
- Composer: full attention, fresh code, targets behavioral parity with OAB as measured by OAB's regression tests
- Internal users accept TS OAB in its current state for ~13 weeks and migrate to Composer when it lands
- If IE adopts Composer later, we contribute from the Python codebase with minimal friction

---

## 2. Scope

**In scope:**
- Full behavioral parity with current TS OAB (all 15 node types, MCP OAuth with all 6 April 2026 fixes, human-in-the-loop, shared MCP servers, multi-LLM support)
- New private repo: `balajir2/composer`, local at `D:\GitHub\composer`
- IE-compatible stack from day 0 (no intermediate stack)
- Fresh, idiomatic Python code — not line-by-line translation of TS
- Backend only — no frontend work during this window
- Completion criterion: OAB's regression suite passes against Composer

**Out of scope (for this design):**
- Integration into IE's monorepo (future decision, separate design)
- User-composed workflow security model (covered separately when/if Composer-in-IE proceeds)
- IE's `agent-commons` Python library reuse — we roll our own equivalent (we don't have access)
- New features beyond current TS OAB's behavior — no scope creep
- Any work on TS OAB — fully frozen
- Frontend work during Phases 0-9 (backend-only window). Frontend fork is Phase 10.

---

## 3. Decisions

### 3.1 Repository

| Item | Decision |
|---|---|
| Name | `composer` |
| Owner | `balajir2` (personal account, matches OAB's host) |
| Visibility | Private |
| Local path | `D:\GitHub\composer` |
| Structure | Backend-only (no frontend in this repo) |
| Relationship to OAB repo | OAB repo is read-only behavioral reference; nothing in Composer imports or depends on OAB |

### 3.2 Stack (IE-compatible from day 0)

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | FastAPI + modern typing |
| Web framework | FastAPI | Matches IE's Python agents |
| ORM | **Prisma Python** | IE uses Prisma; strict stack parity |
| Database | Postgres 15+ | IE's standard |
| Migrations | Prisma migrate | Native to Prisma |
| Orchestration | LangGraph Python | Replaces `@langchain/langgraph` JS |
| LLM abstractions | `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq` | Matches IE's LangChain usage; provider quirks handled upstream; auto LangSmith tracing |
| Validation | Pydantic v2 | Standard with FastAPI |
| Auth | JWT (HS256) middleware matching IE's `DES-004` pattern | Locally signed for development; swapable issuer when IE absorbs |
| Real-time | SSE (Phase 1) + WebSocket (Phase 2, matching IE's `DES-007`) | SSE is simpler for initial phases |
| Encryption | `cryptography` (AES-256-GCM) | Portable; compatible with IE's encryption service |
| Expressions | `simpleeval` | Safe evaluation for if-else/while conditions |
| Code sandbox | `e2b_code_interpreter` | Matches TS OAB |
| Tests | `pytest` + `httpx.MockTransport` + `pytest-asyncio` | Standard Python |
| Tooling | `ruff` (lint+format), `pyright` (type check), GitHub Actions CI | Convention |

### 3.3 Staffing

Solo engineer (Balaji) + AI assistance (Claude). No additional hires or contractors for the initial rebuild.

### 3.4 TS OAB status during the rebuild

**Frozen. Zero commits. Read-only reference.**

No bug fixes, no feature work, no refactoring. If a critical issue is discovered, it is fixed in Composer when Composer's code reaches that area — not in TS OAB. Internal Bounteous users accept TS OAB in its current state for the duration of the rebuild.

---

## 4. Repository Layout

```
composer/                              # D:\GitHub\composer (balajir2/composer on GitHub)
├── src/
│   ├── main.py                        # FastAPI app entrypoint
│   ├── api/
│   │   ├── __init__.py
│   │   ├── workflows.py               # Workflow CRUD
│   │   ├── executions.py              # Execute / resume / stream
│   │   ├── mcp_oauth.py               # OAuth authorize / callback
│   │   ├── test_connection.py         # MCP test connection
│   │   └── auth.py                    # JWT verification middleware
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── langgraph_executor.py      # Execution orchestrator
│   │   ├── state.py                   # WorkflowState Pydantic model
│   │   └── graph_builder.py           # Workflow JSON → LangGraph StateGraph
│   ├── executors/                     # One file per node type
│   │   ├── agent.py
│   │   ├── mcp.py
│   │   ├── http.py
│   │   ├── transform.py
│   │   ├── extract.py
│   │   ├── logic.py                   # if-else, while
│   │   ├── approval.py                # user-approval
│   │   ├── set_state.py
│   │   ├── guardrails.py
│   │   ├── note.py                    # documentation-only
│   │   ├── vector_db.py
│   │   ├── gamma.py
│   │   └── arcade.py
│   ├── mcp/
│   │   ├── client.py                  # HTTP JSON-RPC client
│   │   ├── oauth.py                   # RFC 8707, PKCE, token refresh
│   │   ├── resolver.py                # Resolve MCP servers for execution
│   │   └── schema_adapter.py          # inputSchema → provider tool format
│   ├── storage/
│   │   ├── models.py                  # Prisma-generated types
│   │   ├── checkpointer.py            # LangGraph Postgres checkpointer
│   │   └── repositories/              # Query helpers
│   ├── security/
│   │   ├── encryption.py              # AES-256-GCM
│   │   ├── expressions.py             # simpleeval wrapper
│   │   └── ssrf.py                    # Private-IP guard for HTTP node
│   ├── integrations/
│   │   ├── tools.py                   # Firecrawl / Tavily / Serper / E2B / Gamma / Arcade
│   │   └── langsmith_config.py        # Runtime LangSmith config (not process.env)
│   └── variable_substitution.py       # Templating engine
├── tests/                             # Mirrors src/ structure
│   ├── unit/
│   ├── integration/
│   └── regression/                    # OAB's regression suite, ported to pytest
├── prisma/
│   └── schema.prisma                  # Data model
├── pyproject.toml
├── docker-compose.yml                 # Local Postgres for dev
├── .env.example
├── .github/workflows/ci.yml           # Lint + typecheck + pytest on PR
├── CHANGELOG.md
├── README.md
└── LICENSE (MIT)
```

---

## 5. Development Approach

### 5.1 How we use OAB as reference

OAB's source is the behavioral specification. When building each Composer module, we read the corresponding OAB area to understand:

- What the feature does (public interface, expected inputs/outputs)
- Edge cases and quirks (from the code and git history)
- Validated patterns (e.g., the six April 2026 MCP fixes)

We do **not**:
- Copy/paste OAB code
- Mirror OAB's file structure when a cleaner structure exists
- Inherit OAB's TypeScript idioms into Python
- Commit to `composer/` and `open-agent-builder/` in the same PR

### 5.2 Behavioral parity guardrails

Given we're rebuilding (not translating), divergence risk is higher. Safeguards:

- **Regression tests are the oracle.** OAB's regression suite (72+ tests) is ported to pytest and runs against Composer's API. A test passes in OAB should pass in Composer with equivalent assertions.
- **Known-good acceptance workflows.** The Highspot MCP workflow we built on 2026-04-14 is the canonical end-to-end acceptance test. If Composer runs it with the same OAuth credentials and produces equivalent output, the MCP stack is correct.
- **Feature checklist.** Before declaring completion, each of the 15 node types is exercised with representative inputs. Outputs are manually spot-checked against TS OAB's output.
- **No scope creep.** Behavioral differences from OAB are either bugs in Composer or explicitly logged as "deliberate improvements" in a CHANGELOG. No silent drift.

### 5.3 Testing during the backend rebuild (Phases 0-9)

No Composer UI exists during Phases 0-9 (weeks 1-10). UI is a separate follow-on phase (Phase 10, weeks 11-13). All backend-phase development is tested via:

- `pytest` for unit + integration tests
- `curl` or Postman for manual API exploration
- Real external services (Highspot MCP, Anthropic API) via integration tests with mocked credentials where needed

This is a deliberate trade — full focus on backend correctness during the rebuild, then a bounded UI phase afterward.

---

## 6. Phased Rebuild Plan

**Backend rebuild: ~10 weeks** (Phases 0-9). **UI fork: ~3 weeks** (Phase 10). **Total to usable Composer: ~13 weeks.** Extend the window if needed; do not compromise scope.

### Phase 0 — Scaffolding (Week 1, 0.5 weeks)

- Create `balajir2/composer` GitHub repo (private)
- Local repo at `D:\GitHub\composer`
- FastAPI skeleton: `main.py`, `/health` endpoint
- Prisma schema initialized (empty tables)
- Postgres running via `docker-compose`
- GitHub Actions CI: lint (ruff) + typecheck (pyright) + tests (pytest)
- `pyproject.toml` with locked dependencies
- `.env.example` and setup docs

**Deliverable:** `/health` returns OK; CI green on empty test suite.

### Phase 1 — Execution engine core (Week 1.5, 1 week)

- `src/engine/state.py` — `WorkflowState` Pydantic model
- `src/engine/langgraph_executor.py` — execution orchestrator
- `src/engine/graph_builder.py` — workflow JSON → LangGraph StateGraph
- `src/executors/start.py`, `src/executors/end.py`
- `src/storage/checkpointer.py` — Prisma-backed LangGraph checkpointer
- Prisma migrations for `Workflow`, `WorkflowExecution`, `LangGraphCheckpoint`, `LangGraphCheckpointWrite`
- Integration test: `Start → End` linear workflow executes end-to-end

**Deliverable:** POST a minimal workflow JSON, execute it, retrieve the execution record via API.

### Phase 2 — Agent node + LLM providers (Week 2.5, 1 week)

- `src/executors/agent.py` — LangChain chat models, tool binding, agentic loop
- All 4 providers bound: Anthropic, OpenAI, Google, Groq
- Provider-specific quirk handling via LangChain built-ins
- Variable substitution (`src/variable_substitution.py`)
- Tests: `Start → Agent → End` passes for all 4 providers with mocked LLMs

**Deliverable:** agent node works without tools. Multi-LLM support verified via tests.

### Phase 3 — MCP client with full OAuth (Week 3.5, 1.5 weeks)

- `src/mcp/client.py` — HTTP JSON-RPC client
- `src/mcp/oauth.py` — OAuth authorize, callback, token exchange, refresh
  - RFC 8707 `resource` parameter on all grants
  - PKCE (S256)
  - Manual tool calling (no Anthropic native `mcp_servers`)
  - `inputSchema` (camelCase) support
  - Server-side OAuth token retrieval
  - Shared MCP servers (`isShared`) with service-account token pattern
- `src/mcp/schema_adapter.py` — MCP tools → LangChain tools
- `src/mcp/resolver.py` — Resolve MCP servers for execution
- `src/api/mcp_oauth.py` — authorize / callback / test-connection endpoints
- `src/executors/mcp.py` — MCP node executor
- Prisma schema: `McpServer`, `McpOAuthToken`, `McpOAuthState`

**Deliverable:** Highspot MCP workflow runs end-to-end via API. Canonical acceptance test.

### Phase 4 — Remaining executors batch 1 (Week 5, 1 week)

- `src/executors/http.py` (with SSRF guard in `src/security/ssrf.py`)
- `src/executors/transform.py` (via E2B Python SDK)
- `src/executors/extract.py` (LangChain `with_structured_output`)
- `src/executors/logic.py` (if-else, while via LangGraph conditional edges + `simpleeval`)
- `src/executors/set_state.py`
- Per-executor tests

**Deliverable:** conditional routing, loops, and transforms functional.

### Phase 5 — Human-in-the-loop + streaming (Week 6, 1 week)

- `src/executors/approval.py` — LangGraph `interrupt()` pattern
- Prisma schema: `Approval`
- `src/api/executions.py` — SSE streaming endpoint with canonical event shapes (`workflow_started`, `node_started`, `node_completed`, `workflow_completed`, etc.)
- Full end-to-end workflow with approval pause/resume executable via API

**Deliverable:** human-in-the-loop works. SSE streaming observable via `curl`.

### Phase 6 — Remaining executors batch 2 (Week 7, 1 week)

- `src/executors/guardrails.py`
- `src/executors/note.py`
- `src/executors/vector_db.py` (Pinecone, Qdrant, Weaviate, Chroma, Milvus)
- `src/executors/gamma.py` (Gamma AI)
- `src/executors/arcade.py` (Arcade browser automation)
- Tests for each

**Deliverable:** all 15 node types functional.

### Phase 7 — API parity + regression suite (Week 8, 1 week)

- All REST endpoints: workflows CRUD, executions CRUD, approvals, OAuth routes, test-connection, upload
- Shared MCP servers (`isShared` feature + server-for-execution resolution)
- Bootstrap script (`scripts/bootstrap.py`) matching today's `bootstrap-convex.cjs` but for Postgres
- OAB regression suite ported to pytest — runs against Composer backend
- Spot-check comparisons: pick five workflows, run on both OAB and Composer, compare outputs manually

**Deliverable:** OAB regression suite passes against Composer.

### Phase 8 — Security + hardening (Week 9, 0.5 weeks)

- Security audit pass: SSRF, expression sandbox, rate limiting, fail-closed auth, secret encryption
- Stress tests on concurrent executions
- Error handling and observability

**Deliverable:** security review passes.

### Phase 9 — Cutover readiness (Week 9.5, 0.5 weeks)

- Data migration script: Convex → Postgres (to preserve existing TS OAB workflows, executions, MCP servers for migrating internal users)
- Deployment docs
- `.env.example` final
- WebSocket streaming (to match IE's `DES-007`, replacing SSE for production)

**Deliverable:** Composer backend is production-ready.

### Phase 10 — UI fork and adaptation (Weeks 11-13, 3 weeks)

- Create `composer/frontend/` directory (or a separate `composer-ui` repo — decide at start of phase)
- Copy OAB's Next.js frontend structure (`app/`, `components/`, `hooks/`, `lib/`) as the starting point
- Replace Convex client layer with a FastAPI/OpenAPI client targeting Composer backend
- Adapt auth integration (NextAuth + Azure AD continues to issue JWTs; Composer's middleware validates)
- Keep all 15 node panels and the React Flow canvas unchanged (they only need the data source swapped)
- Swap real-time data source from Convex subscriptions to SSE/WebSocket from Composer
- Update environment variables and build configuration
- Port Playwright tests to target Composer
- Visual QA across the builder, settings panels, and execution viewer

**Deliverable:** end-to-end Composer product: users log in, compose workflows visually, execute them, watch real-time output — all running on the Python/FastAPI backend.

---

## 7. Completion Criteria

**Backend (Phases 0-9) is "done" when all of these are true:**

- [ ] All 15 node types executable end-to-end via API
- [ ] OAB's regression suite (72+ tests) ported to pytest and passes against Composer
- [ ] Highspot MCP workflow runs end-to-end including OAuth (canonical acceptance test)
- [ ] Shared MCP servers (`isShared`) work with service-account token pattern
- [ ] Human-in-the-loop (user-approval) works with checkpoint/resume
- [ ] All 4 LLM providers (Anthropic, OpenAI, Google, Groq) tested
- [ ] SSE streaming works; WebSocket streaming works (matches IE `DES-007`)
- [ ] Security audit passes (SSRF, expression sandbox, secret encryption, fail-closed auth)
- [ ] Bootstrap script provisions a new Postgres deployment from scratch
- [ ] Data migration script tested on current Convex dev data
- [ ] Five spot-check workflows (including HighSpotMCP) produce equivalent outputs on OAB and Composer

**UI (Phase 10) is "done" when:**

- [ ] User can sign in, view their workflows, create/edit/save a workflow on the visual canvas
- [ ] All 15 node types have working configuration panels
- [ ] User can execute a workflow and watch real-time progress with node-by-node status
- [ ] Settings UI for MCP servers, OAuth connections, and API keys works
- [ ] The HighSpotMCP workflow runs visually (not just via API) end-to-end
- [ ] Playwright test suite passes against the Composer frontend + backend

---

## 8. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| LangGraph JS ↔ Python semantic differences (conditional edges, interrupts, state reducers) | High | Fresh design in Python using LangGraph Python's idioms. Regression tests catch behavioral drift. Side-by-side spot-checks on representative workflows. |
| Prisma Python + async FastAPI + LangGraph checkpointer integration issues | Medium | Spike the checkpointer in Phase 0 before deep commitment. Escape hatch: switch to SQLAlchemy if the integration is painful. Decision gate at end of Phase 1. |
| Fresh rebuild introduces subtle behavioral divergence from OAB | High (but contained) | Regression suite is the ultimate safety net — port it early (parallel with Phase 1–3) so tests are ready when features are. Spot-check five canonical workflows manually. |
| Scope creep — tempting to "improve" beyond OAB's behavior during rebuild | Medium | No scope creep rule. Any improvements explicitly noted in CHANGELOG as deliberate divergences. |
| Rebuild takes longer than 13 weeks | Medium | Path A (agreed): extend the window, don't compromise scope. |
| AI-assisted work introduces subtle bugs | Medium | Regression tests, human review of every PR, spot-checks on complex flows. |
| Data migration from Convex to Postgres has edge cases | Medium | Build and test the migration script in Phase 9 against current Convex dev dataset. Fail fast on malformed records. |
| A TS OAB user hits a hard blocker during the freeze | Low | Accepted risk per Rule 2. Internal users know Composer is coming. |

---

## 9. What This Design Does Not Answer

Intentionally deferred:

- **Frontend repo location** — at start of Phase 10, decide whether `composer/frontend/` (same repo) or a separate `composer-ui` repo is cleaner. Either works; committing to one now is premature.
- **Production deployment architecture** (Vercel, AWS, IE's infra?) — decided after Phase 10.
- **Security model for user-composed workflows** — David's concern #2. Relevant when Composer-in-IE is proposed; not during this rebuild.
- **Cutover plan for TS OAB internal users** — decided near end of Phase 10.
- **IE MCP adapter integration** — when IE ships their MCP adapter, we register it in Composer as any other MCP server. No special design work here.
- **Composer-inside-IE merge** — happens (or doesn't) post-parity as a separate decision.

---

## 10. Communication Plan

**During the rebuild (weeks 1–13):**
- No proactive communication to David or IE
- The Composer design docs from 2026-04-15 remain our public position
- If David asks for updates, the honest answer is: "We're modernizing OAB's stack; we'll have more to say when it's ready"

**At parity — end of Phase 9 (around week 10):**
- Optional early reveal if IE's MCP adapter is ready and a demo opportunity exists. Backend-only demo via the API + a simple curl trace can suffice.

**At full product — end of Phase 10 (around week 13):**
- Decision point: reveal Composer to IE
- We have a working Python OAB on IE's stack with a functional visual UI, ready to plug in.
- Factors: state of IE's MCP adapter, pilot data, the Composer-in-IE conversation

---

## 11. Next Step

Invoke the writing-plans skill to produce the detailed Phase 0 implementation plan. Subsequent phase plans are produced as each phase approaches.
