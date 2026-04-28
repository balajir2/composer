# Open Agent Builder — Reference Architecture for Composer Port

**Purpose:** Self-contained technical reference for the IE engineering team rewriting Open Agent Builder (OAB) as **Composer**, a module inside the Intelligent Engineering monorepo. Assumes no prior familiarity with OAB.

**Date:** 2026-04-15
**Source repo:** https://github.com/balajir2/open-agent-builder
**Target:** `apps/composer/` inside `Bounteous-Inc/intelligent-engineering`

---

## 1. What OAB Is (in one paragraph)

Open Agent Builder is a visual, low-code platform for composing AI workflows. Users drag nodes onto a canvas (Start → Agent → MCP → If/Else → End), configure each node, and execute the result as a LangGraph state machine. Each node type corresponds to a concrete execution unit: an LLM call, an MCP tool invocation, an HTTP request, a JavaScript transform, a human approval gate, etc. Workflows stream execution events in real time via Server-Sent Events so users watch nodes light up as they run. It supports 4 LLM providers (Anthropic, OpenAI, Google, Groq) and MCP-based tool servers (including OAuth 2.0 flows). Think "Zapier for AI agents" — but with a real execution engine behind it.

---

## 2. Current Tech Stack (and how it maps to IE)

| Layer | OAB today | Composer target in IE |
|---|---|---|
| Frontend framework | Next.js 16 + React 19 + TypeScript | **Keep as-is** — becomes `apps/composer/composer-ui/` |
| Visual builder | React Flow | **Keep as-is** — no Python equivalent exists |
| Backend runtime | Next.js API routes (TypeScript) | **Replace with Python / FastAPI** — `apps/composer/composer-service/` |
| Execution engine | LangGraph JS | **Port to LangGraph Python** (more mature lib) |
| Database | Convex (real-time NoSQL) | **Replace with Prisma / Postgres** (IE's standard) |
| Real-time | Convex live queries + SSE | **IE's WebSocket protocol** (`DES-007`) + SSE fallback |
| Auth | NextAuth.js + Azure AD | **IE JWT** (`DES-004`) + **IE RBAC** (`DES-005`) |
| Encryption | AES-256-GCM via Convex env | **IE secret management** (`DES-026`) |
| Agent catalog | OAB's own list | **Maestro's agent catalog** |
| MCP registry | OAB's `mcp-registry.ts` | **IE's MCP orchestrator** (`DES-008`) |
| LangSmith tracing | Optional via env vars | Keep — orthogonal to stack |

---

## 3. The 15 Node Types (What Composer Must Support on Day 1)

**Core (10):**
- `start` — entry point; declares input variables
- `agent` — LLM call with optional tool/MCP attachments
- `mcp` — direct MCP tool invocation (not via agent reasoning)
- `extract` — LLM-powered structured data extraction with schema
- `http` — HTTP API call
- `transform` — JavaScript code (sandboxed)
- `if-else` — conditional branching
- `while` — loop until condition
- `user-approval` — human-in-the-loop pause
- `end` — terminal node

**Additional (5):**
- `set-state` — direct variable manipulation
- `guardrails` — content moderation / safety checks
- `arcade` — Arcade tool integration (browser automation)
- `gamma-ai` — Gamma presentation/doc generation
- `note` — documentation-only node (no execution)

**What to keep for Composer v1:**
All 10 core + `set-state`, `guardrails`, `note`. Defer `arcade` and `gamma-ai` unless there's demand. These are proprietary 3rd-party integrations that aren't core to SDLC workflows.

---

## 4. Key Architectural Concepts

### 4.1 Workflow as a LangGraph StateGraph

A saved workflow is a JSON document of `nodes[]` and `edges[]`. At execution time, the executor converts it to a LangGraph `StateGraph`:

```
Workflow JSON → StateGraph → compile → invoke/stream
```

Each node becomes a function in the graph. Edges become conditional or direct transitions. State is a shared object (`variables`, `nodeResults`, etc.) that accumulates results.

**Key file to read first:** [`lib/workflow/langgraph.ts`](../../../lib/workflow/langgraph.ts) (~1,400 lines). This is the heart of OAB. The Python port is ~80% of the engineering effort.

### 4.2 Execution state

LangGraph state is append-only via reducers. Never mutate. Every node returns a partial state update that gets merged. This is critical for checkpointing (resume after human approval requires replay-safe state).

### 4.3 Checkpointing

OAB uses a Convex-based `ConvexCheckpointSaver` (implements LangGraph's checkpointer interface). For Composer: implement a **Postgres-based checkpointer** using SQLAlchemy. The interface is well-defined by LangGraph — 4 methods: `put`, `get`, `list`, `put_writes`.

Schema delta (new Prisma tables):
```
workflow
workflow_execution
langgraph_checkpoint
langgraph_checkpoint_write
mcp_oauth_token
mcp_oauth_state
approval
```

### 4.4 Real-time execution streaming

Clients watch workflow execution via SSE. Every node transition emits events: `workflow_started`, `node_started`, `node_completed`, `node_failed`, `workflow_completed`, `error`. For Composer, map these to IE's WebSocket protocol with SSE as a fallback for simple integrations.

### 4.5 Variable substitution

OAB has a templating system: `{{input.foo}}`, `{{lastOutput}}`, `{{nodeResults.node_3.output}}`. Users reference these inside agent instructions, HTTP bodies, conditional expressions, etc. See [`lib/workflow/variable-substitution.ts`](../../../lib/workflow/variable-substitution.ts). **Direct port to Python** — no architectural change needed.

### 4.6 Safe expression evaluation

`if-else` and `while` conditions, plus `set-state` expressions, are evaluated via **mathjs** (not `Function()`) to prevent code injection. Python equivalent: `simpleeval` or a restricted `asteval`. Never use `eval()` or `exec()`.

### 4.7 File content extraction

Start nodes accept file uploads (PDF, DOCX, Markdown). OAB pre-fetches and extracts text content, injecting it into state as `content` / `text`. See [`lib/workflow/file-utils.ts`](../../../lib/workflow/file-utils.ts). Python equivalent: `pypdf`, `python-docx`, built-in text reader.

---

## 5. MCP Integration (Most Battle-Tested Part)

OAB just completed an intense Highspot OAuth integration that surfaced 6 architectural bugs. All of these lessons must carry into the Python port:

### 5.1 OAuth 2.0 support

- Authorization Code flow with **PKCE (S256)**
- Client Credentials flow
- **RFC 8707 `resource` parameter** — required by servers like Highspot; must be on authorization, token exchange, AND refresh requests
- Tokens encrypted with AES-256-GCM before storage
- Auto-refresh with 5-minute buffer before expiry

### 5.2 Manual tool calling (NOT Anthropic native `mcp_servers`)

**Critical learning:** Anthropic's native `mcp_servers` connector fails on servers returning large tool definitions (Highspot's `tools/list` exceeds 73K chars and triggers a JSON parse error). **Do not use native `mcp_servers`.** Instead:

1. Call `tools/list` ourselves via HTTP JSON-RPC
2. Convert tool definitions to regular tool format
3. Pass as normal `tools` to the LLM
4. Execute tool calls via HTTP JSON-RPC ourselves

This is the pattern in [`lib/workflow/executors/mcp-utils.ts`](../../../lib/workflow/executors/mcp-utils.ts). Replicate in Python.

### 5.3 Tool schema mapping

MCP spec uses `inputSchema` (camelCase). Support both `inputSchema`, `schema`, and `input_schema` in the converter — different servers use different keys.

### 5.4 Provider-specific tool formats

Each LLM provider expects a different tool format. The executor maps between them:
- Anthropic: `content[].type === 'tool_use'`, `input` field
- OpenAI/Groq: `choices[0].message.tool_calls[]`, `function.arguments` JSON string
- Google: `parts[].functionCall`, `args` field

Python LangChain has this abstraction built-in via `bind_tools()` on chat models.

### 5.5 Server-side token retrieval

Never send OAuth tokens to the client. Test-connection and execution always retrieve tokens server-side from the DB. This is how we protect against token leakage and support auto-refresh transparently.

---

## 6. Node Executors — Port Priority

Each node type has a dedicated executor in [`lib/workflow/executors/`](../../../lib/workflow/executors/). Port order recommendation:

| Priority | Executor | Notes |
|---|---|---|
| 1 | `agent.ts` | Largest; Python has better LangChain support |
| 1 | `mcp.ts` + `mcp-utils.ts` | Port all OAuth/manual-calling logic |
| 1 | `http.ts` | Straightforward; use `httpx` |
| 2 | `extract.ts` | LLM structured output with Pydantic schema |
| 2 | `data.ts` (transform) | Uses E2B sandbox; E2B has Python SDK |
| 2 | `logic.ts` (if-else, while) | Use `simpleeval` for expressions |
| 3 | `tools.ts` + `tool-factory.ts` | Firecrawl, Tavily, Serper, Gamma — all have Python SDKs |
| 3 | `guardrails.ts` | Content moderation |
| 3 | `vector-db.ts` | Pinecone/Qdrant/Weaviate/Chroma/Milvus — all Python-first |
| Defer | `arcade.ts`, `gamma.ts` | Not core SDLC |

---

## 7. Security Architecture (Must Carry Over)

Open Agent Builder has been through multiple security hardening passes. Critical patterns:

- **Zod validation** on all API inputs → use **Pydantic** in Python
- **SSRF protection** in HTTP node (block private IPs, metadata endpoints)
- **mathjs** for expressions (not `eval`) → use `simpleeval` / `asteval`
- **E2B sandboxes** for user-provided code → keep; has Python SDK
- **DOMPurify** on rendered HTML → use `bleach` in Python where applicable
- **AES-256-GCM** for secret storage → Python `cryptography` library
- **Fail-closed auth** — when uncertain, deny; never fall back to unauthenticated
- **Prototype pollution protection** → less of an issue in Python, but validate JSON inputs
- **Rate limiter** with fail-closed behavior on critical endpoints

---

## 8. LLM Provider Specifics (Gotchas)

Summarized in [`CLAUDE.md`](../../../CLAUDE.md) in detail. Key points:

- **Anthropic reasoning models** (o1, o3, gpt-5*) use `max_completion_tokens` not `max_tokens` → handled per-provider in OAB
- **Google Gemini** rejects tool schemas with non-standard fields (`examples`, `default`, `arguments`) → must strip before sending
- **Model IDs change frequently** — check official docs before updating. Current supported set is in [`lib/config/llm-config.ts`](../../../lib/config/llm-config.ts)

**Python LangChain equivalents:**
- `ChatAnthropic`, `ChatOpenAI`, `ChatGoogleGenerativeAI`, `ChatGroq`
- These handle most provider gotchas automatically, which is a strong argument for the Python port

---

## 9. What NOT to Port

Drop these from Composer v1:

- **NextAuth.js + Azure AD auth layer** — IE has its own JWT auth
- **Convex-specific files** in `convex/` — rewrite as Prisma schema + FastAPI services
- **UI Builder** (`/ui-builder` route) — OAB's drag-drop UI creator; interesting but not core for SDLC use case
- **Standalone `/workflow-runner` and `/ui-user-workflows` pages** — replaced by IE's Cue chat UI or Chorus embed
- **Two-tier API key management UI** — replaced by IE's tenant/user secret management
- **Standalone sign-in/sign-up pages** — IE auth handles this
- **Firecrawl-as-official-MCP** registry entry — IE has its own MCP management

---

## 10. The "Must Preserve" List

Port these exactly — they are the product:

1. **Visual workflow builder UI** — React Flow canvas, 15 node types, drag-drop, connect edges, save/load
2. **LangGraph executor** — StateGraph construction from workflow JSON
3. **MCP client with OAuth 2.0 support** — with all 6 fixes from April 2026 (RFC 8707 resource, manual tool calling, inputSchema mapping, server-side token retrieval, test-connection OAuth, resolver auth)
4. **Real-time execution streaming** — SSE or WebSocket
5. **Human-in-the-loop (user-approval node)** — including checkpoint/resume
6. **Variable substitution** — templating engine
7. **File extraction** — PDF, DOCX, Markdown
8. **Multi-LLM support** — Claude/GPT/Gemini/Groq at minimum
9. **Template library** — starter workflows
10. **LangSmith tracing hookup** — configurable via IE's secret management

---

## 11. Critical Files for IE Engineers to Read First

Read in this order:

1. [`CLAUDE.md`](../../../CLAUDE.md) — project overview, conventions, LLM provider gotchas
2. [`lib/workflow/types.ts`](../../../lib/workflow/types.ts) — workflow/node data model
3. [`lib/workflow/langgraph.ts`](../../../lib/workflow/langgraph.ts) — the executor (heart of the system)
4. [`lib/workflow/executors/agent.ts`](../../../lib/workflow/executors/agent.ts) — agent node (the most complex executor)
5. [`lib/workflow/executors/mcp-utils.ts`](../../../lib/workflow/executors/mcp-utils.ts) — MCP client
6. [`convex/schema.ts`](../../../convex/schema.ts) — data model to port to Prisma
7. [`convex/mcpOAuthTokensActions.ts`](../../../convex/mcpOAuthTokensActions.ts) — OAuth flows with all 6 April 2026 fixes
8. [`app/api/workflows/[workflowId]/execute-stream/route.ts`](../../../app/api/workflows/%5BworkflowId%5D/execute-stream/route.ts) — canonical execution entry point

---

## 12. Testing

- OAB uses **Playwright** for E2E; IE uses **Turbo + Vitest/Jest** mostly
- For Composer, use IE's existing test infrastructure
- Reimplement the ~158 model regression tests in Python (`pytest` + `httpx` mocks)
- Security tests (ownership, fail-closed auth, rate limiter) should be re-created against FastAPI routes

---

## 13. Lessons from April 14-15 2026 (The Highspot Debug Session)

These six fixes took one long session to find. The Python port must include them from day one:

| Lesson | Where it surfaced |
|---|---|
| RFC 8707 `resource` parameter required on token exchange + refresh, not just authorization | Highspot rejecting token exchange with "resource mismatch" |
| Test-connection endpoint must retrieve OAuth tokens from DB server-side for OAuth-type servers | "Authentication failed - check access token" on connection test |
| Unauthenticated Convex clients silently fail auth-required queries, returning `[]` | Agent "hallucinating" tool calls because no tools were injected |
| Anthropic's native `mcp_servers` connector breaks on large tool definitions | Highspot's tools/list (73K chars) caused JSON parse error at column 73579 |
| MCP spec uses `inputSchema` (camelCase) — must support alongside `schema` / `input_schema` | Tool parameters coming through empty |
| LangSmith config must be explicitly threaded through execution, not relied on from `process.env` in production | Tracing silently disabled in production |

See the full commit history around April 14-15 2026 for context.

---

## 14. Glossary

- **MCP** — Model Context Protocol; standardized way for LLMs to call external tools
- **LangGraph** — Stateful multi-actor orchestration framework (LangChain family)
- **StateGraph** — LangGraph's core abstraction; nodes + edges + state reducer
- **Checkpointer** — Persistence layer for LangGraph state between executions (required for human-in-the-loop)
- **SSE** — Server-Sent Events; one-way streaming from server to client
- **OAuth 2.0 Authorization Code with PKCE** — Standard user-facing OAuth flow with code verifier (recommended for MCP)
- **RFC 8707** — OAuth 2.0 Resource Indicators; required `resource` param on authorization and token endpoints
- **Two-tier API keys** — User-specific keys override system-wide fallback keys (pattern throughout OAB)

---

## Appendix: Recommended Python Libraries

| Purpose | Library |
|---|---|
| Web framework | FastAPI |
| LLM orchestration | LangGraph (`langgraph`), LangChain (`langchain`, `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq`) |
| HTTP client | `httpx` |
| Postgres ORM | SQLAlchemy or Prisma Python (prefer SQLAlchemy for LangGraph checkpointer compatibility) |
| Encryption | `cryptography` |
| Validation | Pydantic |
| Safe expressions | `simpleeval` |
| PDF extraction | `pypdf` |
| DOCX extraction | `python-docx` |
| Code sandbox | E2B Python SDK (`e2b_code_interpreter`) |
| WebSocket | FastAPI built-in |
| LangSmith | `langsmith` |
