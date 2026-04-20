# Composer — Engineering Blueprint

**Audience:** IE engineering team
**Purpose:** Technical architecture and integration design for Composer as a Module inside the Intelligent Engineering monorepo
**Date:** 2026-04-15
**Companion doc:** `2026-04-15-composer-01-oab-reference-architecture.md` (read this first for OAB context)

---

## 1. Goal

Port the visual workflow builder and LangGraph execution engine from Open Agent Builder into IE as a new module at `apps/composer/`. Replace OAB's Convex + NextAuth stack with IE's Prisma/Postgres + JWT auth. Integrate with IE's MCP orchestrator (`DES-008`), agent catalog (Maestro), tenancy model, and WebSocket protocol (`DES-007`).

---

## 2. Scope of Phase 1 (MVP)

**In scope:**
- New monorepo app: `apps/composer/`
- Backend: Python / FastAPI service (`composer-service`)
- Frontend: Next.js / TypeScript UI (`composer-ui`) — React Flow-based
- 12 node types: Start, End, Agent, MCP, HTTP, Transform, Extract, If/Else, While, User-Approval, Set-State, Guardrails, Note
- MCP OAuth 2.0 (with all 6 fixes from April 2026)
- Multi-LLM: Claude, OpenAI, Google, Groq
- Real-time execution streaming via IE's WebSocket protocol + SSE fallback
- Agent catalog sourced from Maestro
- MCP registry sourced from IE's MCP orchestrator
- Per-tenant workflow storage with IE's RBAC
- LangSmith tracing (configurable via IE secret management)

**Out of scope for Phase 1:**
- UI Builder (OAB's drag-drop UI designer)
- Arcade and Gamma AI nodes (SDLC-irrelevant)
- Workflow template marketplace (Phase 2)
- Cross-tenant template sharing (Phase 4)
- Mobile / responsive optimization beyond basic support

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     IE Monorepo                                 │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │    Maestro    │  │    Chorus     │  │     Cue      │         │
│  │   (platform)  │  │   (tenant)    │  │   (chat)     │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                  │                  │                  │
│         └──────────┬───────┴──────────────────┘                 │
│                    │                                            │
│  ┌─────────────────┼──────────────────────────────┐             │
│  │           IE Platform Services                 │             │
│  │  - JWT Auth       - MCP Orchestrator           │             │
│  │  - RBAC / Tenancy - Secret Management          │             │
│  │  - WebSocket      - Rate Limiting              │             │
│  └─────────────────┬──────────────────────────────┘             │
│                    │                                            │
│  ┌─────────────────┴──────────────────────────────┐             │
│  │              Composer (NEW)                    │             │
│  │                                                │             │
│  │  ┌─────────────────┐    ┌──────────────────┐  │             │
│  │  │  composer-ui    │    │ composer-service │  │             │
│  │  │  (Next.js, TS)  │───▶│ (FastAPI, Py)    │  │             │
│  │  │  React Flow     │    │ LangGraph Python │  │             │
│  │  └─────────────────┘    └────────┬─────────┘  │             │
│  │                                   │            │             │
│  │                          ┌────────┴─────────┐  │             │
│  │                          │  agent-commons   │  │             │
│  │                          │  (shared Python) │  │             │
│  │                          └──────────────────┘  │             │
│  └────────────────────────────────────────────────┘             │
│                    │                                            │
│  ┌─────────────────┴──────────────────────────────┐             │
│  │         Shared Postgres (Prisma schema)        │             │
│  │  + Composer-specific tables (see Section 6)    │             │
│  └────────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Module Layout

```
apps/composer/
├── composer-ui/                    # Next.js 14+, TypeScript
│   ├── app/
│   │   ├── workflows/              # Workflow CRUD pages
│   │   ├── workflows/[id]/edit/    # Visual builder canvas
│   │   ├── workflows/[id]/runs/    # Execution history + live stream
│   │   └── templates/              # Template browser
│   ├── components/
│   │   ├── workflow-builder/       # React Flow canvas + node panels (ported from OAB)
│   │   ├── execution-panel/        # Live execution viewer
│   │   └── ui/                     # IE-themed UI primitives
│   ├── hooks/
│   │   └── useWorkflowExecution.ts # WebSocket stream consumer
│   └── lib/
│       ├── api-client.ts           # Talks to composer-service
│       └── ie-auth.ts              # Consumes IE JWT
│
├── composer-service/               # Python 3.11+, FastAPI
│   ├── src/
│   │   ├── main.py                 # FastAPI app entry
│   │   ├── routes/
│   │   │   ├── workflows.py        # CRUD
│   │   │   ├── executions.py       # Execute / resume / stream
│   │   │   ├── mcp_oauth.py        # OAuth authorize/callback
│   │   │   └── test_connection.py  # MCP connection test
│   │   ├── engine/
│   │   │   ├── langgraph_executor.py    # Port of lib/workflow/langgraph.ts
│   │   │   ├── state.py                  # WorkflowState annotation
│   │   │   └── graph_builder.py          # JSON → StateGraph
│   │   ├── executors/
│   │   │   ├── agent.py
│   │   │   ├── mcp.py
│   │   │   ├── http.py
│   │   │   ├── transform.py
│   │   │   ├── extract.py
│   │   │   ├── logic.py              # if-else, while
│   │   │   ├── approval.py
│   │   │   ├── guardrails.py
│   │   │   └── set_state.py
│   │   ├── mcp/
│   │   │   ├── client.py             # JSON-RPC client with OAuth
│   │   │   ├── oauth.py              # RFC 8707 resource param, PKCE
│   │   │   ├── resolver.py           # Resolve MCP servers for execution
│   │   │   └── schema_adapter.py     # inputSchema → provider tool format
│   │   ├── storage/
│   │   │   ├── models.py             # SQLAlchemy models
│   │   │   ├── checkpointer.py       # LangGraph Postgres checkpointer
│   │   │   └── repositories/         # CRUD operations
│   │   ├── security/
│   │   │   ├── auth.py               # IE JWT verification middleware
│   │   │   ├── rbac.py               # Tenant/role checks
│   │   │   ├── encryption.py         # AES-256-GCM for secrets
│   │   │   └── expressions.py        # simpleeval wrapper (if-else/while)
│   │   ├── integrations/
│   │   │   ├── maestro_agents.py     # Fetch agent catalog from Maestro
│   │   │   ├── mcp_orchestrator.py   # Use IE MCP orchestrator
│   │   │   └── langsmith_config.py   # Runtime LangSmith config
│   │   └── variable_substitution.py  # Templating engine
│   ├── tests/
│   ├── migrations/                   # Prisma or Alembic migrations
│   └── pyproject.toml
│
└── README.md
```

---

## 5. Auth and Tenancy Integration

### 5.1 Request flow

1. User hits `composer-ui` route
2. Next.js middleware checks IE JWT cookie (same cookie as Maestro/Chorus/Cue)
3. UI calls `composer-service` REST/WebSocket with JWT in `Authorization: Bearer <token>`
4. FastAPI middleware validates JWT using IE's shared auth verifier (per `DES-004`)
5. Request context includes `userId`, `tenantId`, `roles[]`
6. All data queries scope to `tenantId`

### 5.2 Authorization rules

| Action | Required role |
|---|---|
| List workflows in tenant | `composer.read` |
| Create / edit / delete workflow | `composer.write` |
| Execute workflow | `composer.execute` |
| Access MCP OAuth settings | `composer.admin` |
| View execution logs | `composer.read` |
| View execution logs for other users in tenant | `composer.admin` |

Roles added to IE's RBAC system (`DES-005`). Mapped to standard tenant roles (`tenant-admin` gets all; `tenant-user` gets read + execute; `tenant-analyst` gets read + write + execute).

### 5.3 Multi-tenancy isolation

- Every Composer table has a mandatory `tenant_id` column
- All queries automatically filter by `tenant_id` via SQLAlchemy query hooks
- Row-level security enforced at the query layer; validated by integration tests

---

## 6. Data Model (Prisma Schema Delta)

New tables added to IE's Postgres schema:

```prisma
model Workflow {
  id          String   @id @default(cuid())
  tenantId    String
  userId      String   // creator
  name        String
  description String?
  nodes       Json     // Array of WorkflowNode
  edges       Json     // Array of WorkflowEdge
  isTemplate  Boolean  @default(false)
  isPublic    Boolean  @default(false)
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt

  executions  WorkflowExecution[]

  @@index([tenantId])
  @@index([userId])
}

model WorkflowExecution {
  id             String    @id @default(cuid())
  workflowId     String
  tenantId       String
  userId         String
  status         String    // running / completed / failed / paused
  input          Json?
  output         Json?
  variables      Json      // Final state variables
  nodeResults    Json      // Per-node results
  error          String?
  startedAt      DateTime  @default(now())
  completedAt    DateTime?
  threadId       String    // LangGraph thread ID

  workflow       Workflow  @relation(fields: [workflowId], references: [id])

  @@index([workflowId])
  @@index([tenantId])
  @@index([status])
}

model LangGraphCheckpoint {
  id            String   @id @default(cuid())
  threadId      String
  checkpointId  String?
  checkpoint    Json
  metadata      Json
  parentConfig  Json?
  createdAt     DateTime @default(now())

  @@index([threadId])
  @@unique([threadId, checkpointId])
}

model LangGraphCheckpointWrite {
  id           String   @id @default(cuid())
  threadId     String
  checkpointId String
  taskId       String
  idx          Int
  channel      String
  value        Json

  @@index([threadId, checkpointId])
}

model ComposerMcpServer {
  // Composer-specific MCP server records (tenant-scoped)
  // If IE's MCP orchestrator already manages MCP servers,
  // this table may be unnecessary — link to it instead
  id                   String   @id @default(cuid())
  tenantId             String
  userId               String
  name                 String
  url                  String
  authType             String   // none | api-key | bearer | oauth
  encryptedAccessToken String?  // AES-256-GCM
  oauthConfigJson      Json?
  tools                Json?    // Cached tool list
  enabled              Boolean  @default(true)
  createdAt            DateTime @default(now())
  updatedAt            DateTime @updatedAt

  @@index([tenantId])
}

model McpOAuthToken {
  id                     String    @id @default(cuid())
  tenantId               String
  userId                 String
  mcpServerId            String
  encryptedAccessToken   String    // AES-256-GCM
  encryptedRefreshToken  String?
  expiresAt              DateTime
  tokenType              String    @default("Bearer")
  scope                  String?
  createdAt              DateTime  @default(now())
  updatedAt              DateTime  @updatedAt

  @@unique([userId, mcpServerId])
  @@index([tenantId])
}

model McpOAuthState {
  // Ephemeral: 10-min TTL, single-use
  state         String   @id
  tenantId      String
  userId        String
  mcpServerId   String?
  codeVerifier  String   // Short-lived; encryption optional
  oauthConfig   Json
  expiresAt     DateTime
  createdAt     DateTime @default(now())
}

model ComposerApproval {
  id             String    @id @default(cuid())
  approvalId     String    @unique
  workflowId     String
  executionId    String
  nodeId         String
  tenantId       String
  userId         String    // who needs to approve
  createdBy      String
  message        String
  status         String    // pending / approved / rejected
  createdAt      DateTime  @default(now())
  respondedAt    DateTime?
  respondedBy    String?

  @@index([status])
  @@index([executionId])
}
```

**Note on existing tables:** If IE's MCP orchestrator (`DES-008`) already manages MCP server registry per-tenant, `ComposerMcpServer` should be replaced by a link to whatever table that uses.

---

## 7. Integration Points

### 7.1 Maestro — Agent Catalog

The Composer UI's "Agent" node configuration fetches the list of available agents from Maestro's API:

```
GET /api/v1/agents?tenantId={tenantId}
→ [ { id, name, description, version, inputSchema, outputSchema } ]
```

User picks an agent; the node stores its `agentId`. At execution time, Composer invokes the agent via IE's existing plugin contract (`DES-014`). Responses stream back through the same WebSocket layer.

### 7.2 MCP Orchestrator (`DES-008`)

Instead of OAB's standalone `convex/mcpServers.ts`, Composer uses IE's existing MCP orchestrator for:
- Registry (list of available MCP servers for the tenant)
- OAuth flow coordination (if IE already has generic OAuth orchestration)
- Tool discovery and invocation

**Open question for the IE team:** Does the MCP orchestrator today already support OAuth 2.0 with RFC 8707 and manual tool calling? If yes, Composer consumes it directly. If no, we add those capabilities to the orchestrator as part of Composer Phase 1 — likely a net benefit for IE overall.

### 7.3 WebSocket Protocol (`DES-007`)

Execution streaming uses IE's WebSocket channel. Event shapes:

```json
{ "type": "workflow_started", "executionId": "...", "tenantId": "...", "timestamp": "..." }
{ "type": "node_started", "executionId": "...", "nodeId": "...", "nodeName": "Agent" }
{ "type": "node_completed", "executionId": "...", "nodeId": "...", "output": {...} }
{ "type": "node_failed", "executionId": "...", "nodeId": "...", "error": "..." }
{ "type": "workflow_completed", "executionId": "...", "output": {...} }
{ "type": "approval_required", "executionId": "...", "approvalId": "...", "message": "..." }
```

Fallback SSE endpoint for simple integrations or environments without WebSocket.

### 7.4 Secret Management (`DES-026`)

Composer does not manage secrets directly. All encryption/decryption goes through IE's secret management service. MCP OAuth tokens, client secrets, and user-provided API keys are stored as `encryptedXXX` blobs encrypted by IE's service.

### 7.5 LangSmith Tracing

LangSmith configuration (`LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, `LANGCHAIN_ENDPOINT`, `LANGCHAIN_TRACING_V2`) is fetched at runtime from IE's config service and passed explicitly to the LangGraph executor (not via `process.env` — learned lesson from OAB's April 2026 debugging).

---

## 8. Python Port — Key Technical Decisions

### 8.1 LangGraph Python (not JS)

LangGraph's Python library is more mature, has more integrations, and matches IE's existing agent pattern. The executor in `engine/langgraph_executor.py` is a direct conceptual port of `lib/workflow/langgraph.ts`:

- `StateGraph` from `langgraph.graph`
- Conditional edges for if-else and while loops
- `interrupt()` for human-in-the-loop (user-approval node)
- `Checkpointer` interface implemented against Postgres

### 8.2 Chat models: LangChain providers

Use LangChain's chat model abstractions instead of raw provider SDKs. This is a **change from OAB** (OAB uses raw Anthropic SDK). Benefits:
- Uniform interface across providers
- Tool binding handled by the library
- Provider-specific gotchas (reasoning models, Gemini schema cleaning) are handled upstream
- **LangSmith tracing works automatically** (solves the tracing gap OAB has)

Libraries: `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq`.

### 8.3 MCP client — manual, not native

**Do not use Anthropic's `mcp_servers` + `betas: ['mcp-client-2025-04-04']` connector.** We learned the hard way that it breaks on servers with large tool definitions (Highspot). Instead:
1. `fetch_mcp_tools(server)` — HTTP JSON-RPC `tools/list` → parse → return tool defs
2. Map to LangChain `Tool` format
3. LLM calls tool → `execute_mcp_tool(server, name, args)` → HTTP JSON-RPC `tools/call`
4. Return result to LLM in the normal tool-result format

### 8.4 OAuth 2.0 with RFC 8707

Implement the six April 2026 fixes from day one:
1. Pass `resource` parameter on authorization URL
2. Pass `resource` on token exchange
3. Pass `resource` on token refresh
4. Pass `resource` on client credentials grant
5. Support `inputSchema` (camelCase) when parsing tool definitions
6. Test-connection endpoint retrieves OAuth tokens server-side

### 8.5 Checkpointer — Postgres via SQLAlchemy

LangGraph defines an abstract `BaseCheckpointSaver`. Implement:
- `put(config, checkpoint, metadata)` → insert into `LangGraphCheckpoint`
- `get(config)` → read most recent checkpoint by `threadId`
- `list(config, limit)` → list checkpoints
- `put_writes(config, writes, task_id)` → insert into `LangGraphCheckpointWrite`

Use async SQLAlchemy (`async def`, `AsyncSession`) to match FastAPI's async model.

### 8.6 Expression evaluator

Replace OAB's `mathjs`-based evaluator with `simpleeval` or `asteval`. Whitelist functions and operators. Never allow attribute access, function calls beyond whitelist, or `import`. Validate test cases against a security fuzz corpus.

### 8.7 Variable substitution

Port `lib/workflow/variable-substitution.ts` to Python. Supports:
- `{{input.foo}}`, `{{lastOutput}}`, `{{nodeResults.node_3.output}}`
- Nested access with dot and bracket notation
- File content injection (when variable references an uploaded file)

---

## 9. File Upload / Extraction

Start nodes accept PDFs, DOCX, and Markdown uploads. Composer delegates file storage to IE's file storage service (if it has one) or Postgres BYTEA / S3. Extraction happens server-side on-demand:

- **PDF** → `pypdf` or `pdfplumber`
- **DOCX** → `python-docx`
- **Markdown** → plain read

Extracted content is injected into state as `variables.inputs.<name>.content`.

---

## 10. Testing Strategy

- **Unit tests:** Each executor (pytest)
- **Integration tests:** Full workflow execution with mocked LLMs and mocked MCP servers (`pytest-asyncio`, `httpx.MockTransport`)
- **Regression tests:** Provider-specific mocks replicating OAB's `tests/model-regression.spec.ts`
- **E2E tests:** Against IE's existing Playwright infrastructure in `apps/composer/composer-ui/`
- **Security tests:** OAuth flow fuzzing, SSRF protection, expression evaluator sandbox escape attempts

Must pass IE's Turbo test pipeline before merge. Aim to contribute ~500 tests to IE's 2,832+ test count.

---

## 11. Phased Delivery (for Faster GTM)

### Phase 1.0 — Walking Skeleton (weeks 1-2)
- `apps/composer/` scaffolding in monorepo
- Empty Next.js UI with IE auth integration
- FastAPI service with one endpoint: `POST /workflows` (create empty workflow)
- Prisma migrations for the 7 new tables

### Phase 1.1 — Execution Engine Core (weeks 3-5)
- LangGraph executor in Python
- Start, Agent, HTTP, End node executors
- One LLM provider (Anthropic via LangChain)
- Postgres checkpointer
- SSE streaming (WebSocket later)

### Phase 1.2 — MCP Integration (weeks 6-7)
- MCP client with OAuth 2.0 (all 6 fixes)
- MCP node executor
- Agent node can attach MCP tools

### Phase 1.3 — Visual Builder (weeks 6-9, parallel with 1.2)
- Port React Flow canvas from OAB
- Node palette and panels for the 12 types
- Save/load workflows
- Execution viewer (live streaming)

### Phase 1.4 — Governance, Tenancy, Integration (weeks 10-11)
- Full RBAC wiring
- Maestro agent catalog integration
- MCP orchestrator integration
- LangSmith tracing
- WebSocket protocol over SSE

### Phase 1.5 — Remaining Nodes + Hardening (weeks 12-13)
- Transform, Extract, If/Else, While, User-Approval, Set-State, Guardrails, Note
- Security audit
- Load testing

### Phase 1.6 — First Customer Pilot (weeks 14+)
- Enable for first tenant
- Support a specific customer workflow end-to-end
- Feedback loop → Phase 2 planning

---

## 12. Open Decisions (for IE Engineering)

1. **Does IE's MCP orchestrator already support OAuth 2.0?** If yes, Composer uses it directly. If no, we enhance the orchestrator as part of this project.
2. **Does IE have a file storage service?** Composer needs one for Start-node uploads; prefer to reuse rather than introduce.
3. **Does IE's rate limiter support per-tenant per-endpoint limits?** Composer execution endpoints must be rate-limited.
4. **Prisma or SQLAlchemy for composer-service?** Prisma's Python support is newer; SQLAlchemy is battle-tested and has better LangGraph checkpointer patterns. **Recommendation: SQLAlchemy.**
5. **Does IE already have Python FastAPI conventions / shared middleware?** `agent-commons/` likely has some; we should reuse aggressively.
6. **WebSocket or SSE first?** SSE is simpler; WebSocket is IE's standard. **Recommendation: ship SSE first for faster dev loop, add WebSocket in Phase 1.4.**
7. **How does Composer surface in the UI?** New top-level nav item ("Workflows"), embedded in Chorus, or standalone route? **Recommendation: start as standalone route under `chorus-ui`, promote to first-class nav once validated.**

---

## 13. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| LangGraph Python behaves differently from JS for edge cases | Medium | Pin versions; port with comprehensive integration tests against known-good OAB outputs |
| Prisma/SQLAlchemy checkpointer has subtle bugs in concurrent executions | Medium | Start with single-execution queue per thread; add concurrency later; stress-test |
| IE's MCP orchestrator lacks OAuth support | Medium | Fall back to Composer owning MCP OAuth tables; revisit in Phase 2 |
| React Flow version drift between OAB and Composer | Low | Pin to OAB's current version initially; upgrade deliberately |
| Python rewrite of OAB's 5,000+ lines of executor logic introduces regressions | Medium-High | Use OAB as a behavioral oracle: same inputs should produce same outputs for golden test cases |
| IE team unfamiliar with LangGraph | Medium | OAB team can pair on executor port; extensive inline docs; one explicit ramp-up week |

---

## 14. Definition of Done for Phase 1

- [ ] `apps/composer/` app exists and passes `npx turbo build` and `npx turbo test`
- [ ] 12 node types executable end-to-end
- [ ] One tenant can create, save, execute, and resume a multi-node workflow using IE's auth
- [ ] Highspot (or equivalent OAuth MCP) connects, authenticates, and is invokable in a workflow
- [ ] LangSmith tracing captures the execution
- [ ] Execution events stream live to the UI
- [ ] Security audit passes (auth, SSRF, expression sandbox, secret encryption)
- [ ] Docs updated (`apps/composer/README.md`, IE's top-level README cross-link)
