# Composer

> **A visual workflow platform for building agentic AI applications.**
> Designers wire nodes on a canvas — LLM agents, HTTP calls, vector DB queries, branching logic, human-approval gates, document upload — and the runtime executes them as resumable state machines with full observability and a real-time stream of node-by-node events.

[![Status](https://img.shields.io/badge/status-production%20ready-success)](docs/overview.md#status)
[![Tests](https://img.shields.io/badge/tests-711%20passing-success)](#testing)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%7C%20Postgres%20%7C%20LangGraph%20%7C%20Next.js-blueviolet)](docs/architecture.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## What Composer does

Composer turns a visual node graph into a running, observable, resumable AI workflow:

1. **Author**: a designer drags nodes onto a React Flow canvas — Start, agents, HTTP, vector DB, if-else, while, human-approval, MCP tool calls, and more — and connects them with edges that carry data and control flow.
2. **Validate**: Composer compiles the graph into a Pydantic-validated workflow definition with size + safety guards (100 node cap, 200 edge cap, sandboxed expression evaluation, sandboxed code execution).
3. **Execute**: at run time, Composer translates the graph into a [LangGraph](https://github.com/langchain-ai/langgraph) state machine, drives it to completion (or to an interrupt for human approval), and persists checkpoints after every node.
4. **Stream**: clients subscribe to the run via WebSocket (`/executions/{id}/ws`) and watch `node_started` → `node_completed` events flow in real time.
5. **Resume**: paused workflows pick up exactly where they left off after a human approval, even across process restarts.
6. **Publish**: a workflow becomes an external API endpoint (`POST /api/run/{slug}`) that customers' systems can call with a per-user API key — sync (block until done) or async (fire-and-forget with a stream URL).

The whole loop is what most agentic-AI teams build from scratch: prompt + tool definitions + retry logic + human approval + audit log + observability. Composer ships it as a single product.

---

## What you can build

| Pattern | What the workflow looks like |
|---|---|
| **RAG pipeline** | Upload a PDF / DOCX / Markdown → in-memory text extraction → chunk + embed → upsert into Pinecone / Qdrant / Chroma / Weaviate / Milvus → at query time, retrieve top-k → grounded LLM answer |
| **Multi-source research agent** | Tavily web search + Firecrawl page scrape → join results → structured-extraction agent → CRM-shaped JSON output |
| **Classify-and-branch ticket triage** | Classifier agent emits `{urgent, category, summary}` JSON → if-else branches on `urgent` → specialist agents draft urgent or standard replies |
| **Document → action items → email** | Upload a meeting transcript → extraction agent emits action items as JSON → drafting agent writes a polished follow-up referencing each item |
| **Human-in-the-loop approval** | Agent drafts content → `user-approval` node pauses execution → reviewer approves or rejects via the runs page → branched routing on the verdict |
| **Slide deck generation** | Tavily-grounded research → Gamma AI node renders a slide deck → final agent surfaces the published URL |
| **Lead enrichment** | Company name in → multi-source research → schema-validated profile out (industry, size, products, recent news, executives, competitors) |
| **Code review assistant** | Diff in → review agent flags issues with severity tags → guardrails screen the *output* for accidental secret leaks → branched delivery |

**17 production-ready reference templates** ship out of the box covering each pattern above and more — see [`docs/designer-guide.md`](docs/designer-guide.md).

---

## Core capabilities

### 18 node types

| Category | Nodes |
|---|---|
| **Boundary** | `start` (workflow input), `end` (terminate), `note` (canvas annotation) |
| **AI / LLM** | `agent` (multi-turn LLM with tool-calling, structured output, MCP support), `extract` (single-shot structured extraction) |
| **Tools / Integration** | `mcp` (Model Context Protocol — static or OAuth-bound), `http` (any external HTTP API), `vector-db` (query + upsert across 5 providers), `gamma-ai` (slide generation), `arcade` (Arcade tools) |
| **Data flow** | `set-state` (write a variable), `transform` (sandboxed expression with optional named output), `data-transform` (collection mapping), `join-chunks` (concatenate text chunks with separator/prefix/suffix) |
| **Control flow** | `if-else` (boolean branch), `while` (bounded loop, max 100 iterations), `user-approval` (pause for human verdict) |
| **Safety** | `guardrails` (LLM-based PII / moderation / jailbreak / hallucination classifiers, runs concurrently) |

### LLM provider support

- **4 providers**: Anthropic Claude, OpenAI, Google Gemini, Groq
- **Per-model verification**: every model in the catalog gets a 1-token probe; auto-disabled on `unavailable` so retired models disappear from designer dropdowns
- **Runtime pre-flight**: agent executor refuses to start on a disabled model with a clear error
- **Centralised key management**: keys live encrypted in Postgres (AES-256-GCM); admin UI tests + rotates them; optional sync to deployment env vars

### Vector database support

- **5 providers** with both query and upsert: Pinecone, Qdrant, Chroma, Weaviate, Milvus
- **OpenAI text-embedding-3-small** for embeddings (configurable)
- **Auto-chunking** for string input on upsert — character window with configurable overlap
- **Stable IDs** for idempotent re-upserts (sha1 / UUIDv5 per provider's native idiom)

### Tool & MCP integration

- **6 built-in tool providers**: Tavily (search), Firecrawl (scrape), Serper (Google search), Browserless (headless Chrome), Gamma (slides), Arcade (tool platform)
- **Full MCP support**: static-auth servers and full OAuth-flow servers (RFC 8707 resource binding for Highspot-grade integrations)
- **Shared MCP servers**: admin can flip an `isShared` flag — one OAuth token serves the team
- **Defensive sanitisation**: MCP responses are scanned for base64-encoded binary content and replaced with metadata-only stubs before the agent sees them (prevents context-window OOM from Highspot xlsx-style fetches)
- **Manual tools/list calling**: bypasses providers' native MCP connector to handle servers with very large tool definitions

### Authentication & authorisation

- **Three-layer auth**: NextAuth v5 (frontend session) → Composer JWT HS256 (backend) → per-user API keys (`ck_...`, bcrypt-hashed) for external invokes
- **SSO support**: Azure AD via NextAuth's AzureAD provider, plus username/password fallback
- **RBAC**: admin / member roles with explicit `Depends(ensure_admin)` enforcement at every API surface
- **404 not 403** on cross-user reads — existence isn't leaked
- **Owner-only delete** even for admins — protects against accidental erasure of another user's work

### Real-time & resilience

- **WebSocket streaming** of `node_started` / `node_completed` / `node_failed` / `workflow_completed` / `approval_required` events
- **Resumable execution** via LangGraph checkpoints stored in Postgres — paused workflows survive process restarts
- **Stuck-execution sweeper** — background task flips abandoned `running` rows to `failed` with explanatory error after a configurable threshold
- **Resilient detached-task wrapper** — async invocations stamp `failed` to the row even on uncaught crash or worker shutdown

### Observability

- **LangSmith tracing** on every workflow run (when configured) — full prompt + tool-call detail
- **Structured JSON logs** with `user_id`, `execution_id`, `workflow_id`, `request_id` fields
- **Execution audit trail** in Postgres — every run, every approval, every API key use
- **OpenAPI spec** auto-generated from FastAPI; consumed by the frontend's typed API client

### Document handling

- **In-memory text extraction** for PDF (pypdf), DOCX (python-docx), Markdown, TXT — no persistence, no S3 dependency
- **Document type** as a first-class Start-node input — designers expose a file picker; the engine sees a regular text variable downstream

### Hardening

- **Sandboxed expression evaluation** with simpleeval — no `eval()`, `exec()`, dunder access, or imports
- **Sandboxed code execution** via `e2b_code_interpreter` for workflows that need real Python
- **AES-256-GCM at rest** for LLM API keys, MCP OAuth tokens, and other secrets
- **In-memory token-bucket rate limiting** per route per actor
- **Size caps**: 100 nodes, 200 edges, 1 MB execution input, 10 MB document upload
- **TLS 1.2+** required everywhere

---

## Quick start

```bash
# 1. Clone + install
git clone https://github.com/balajir2/composer.git
cd composer
uv sync --all-extras
cd frontend && npm install && cd ..

# 2. Configure
cp .env.example .env
# Edit .env: DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, ANTHROPIC_API_KEY (or any LLM provider)

# 3. Database + reference templates
uv run prisma generate
uv run prisma migrate deploy
uv run python -m scripts.seed_templates

# 4. Run
uv run uvicorn src.main:app --reload --port 8000   # backend
cd frontend && npm run dev                          # frontend (in another terminal)
```

Open http://localhost:3000 and click into the Templates gallery to start building. Full walkthrough: [`docs/getting-started.md`](docs/getting-started.md).

In **dev mode** (no `ENVIRONMENT=production` env var set) Composer accepts an unauthenticated fallback user `dev`, so you can start clicking around immediately without provisioning Azure SSO.

---

## How execution works

```
┌────────────────────────────────────────────────────────────────────┐
│                  Browser (Next.js 14 frontend)                     │
│   Designer canvas (React Flow)  •  Runs page  •  Admin console     │
└────────┬─────────────────────────┬─────────────────────────┬───────┘
         │ JWT (NextAuth)          │ WebSocket               │ JWT
         ▼                         ▼                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│              FastAPI backend (src/main.py routers)                   │
│  /workflows /executions /api/run/{slug} /uploads /admin/* /events    │
└────────┬──────────────┬──────────────┬──────────────┬────────────────┘
         │              │              │              │
         ▼              ▼              ▼              ▼
   ┌──────────┐    ┌─────────┐    ┌──────────┐   ┌──────────────┐
   │LangGraph │    │Postgres │    │   MCP    │   │     LLM      │
   │executor  │◄───┤(Prisma) │    │ servers  │   │  providers   │
   │+executors│    │         │    │(HTTP/SSE)│   │ (Anthropic / │
   │          │    │         │    │          │   │  OpenAI/...) │
   └──────────┘    └─────────┘    └──────────┘   └──────────────┘
```

A workflow is a JSON pair of `nodes[]` + `edges[]`. The executor:

1. **Loads** the workflow row, validates against a Pydantic discriminated union over node `type`.
2. **Compiles** a LangGraph `StateGraph`. Plain edges become plain edges; `if-else` / `while` / `user-approval` edges become conditional edges keyed off the source node's branch label.
3. **Runs** `compiled.ainvoke(...)`. Each node receives the current state and returns a delta; LangGraph's reducers merge deltas back. Checkpoints persist after every node.
4. **Pauses** at `user-approval` via LangGraph's `interrupt()`. The execution row flips to `waiting_approval`; the original API call returns; the run resumes when `POST /executions/{id}/resume` arrives.
5. **Emits** events to a shared in-process bus. The WebSocket endpoint subscribes and streams events to the client.
6. **Persists** the terminal state (status, output, variables, node results, error) to the execution row before returning.

Deeper architecture: [`docs/architecture.md`](docs/architecture.md).

---

## Stack

| Layer | Choice |
|---|---|
| **Backend runtime** | Python 3.11 / 3.12, FastAPI, Pydantic v2 |
| **ORM** | Prisma Python (schema-first, typed client) |
| **Database** | Postgres 15+ (Neon-compatible, includes PITR) |
| **Orchestration** | LangGraph Python + LangChain |
| **LLM providers** | Anthropic Claude · OpenAI · Google Gemini · Groq |
| **Vector DBs** | Pinecone · Qdrant · Chroma · Weaviate · Milvus (query + upsert) |
| **Tool providers** | Tavily · Firecrawl · Serper · Browserless · Gamma · Arcade · MCP (static + OAuth) |
| **Frontend** | Next.js 14 App Router, Tailwind, shadcn/ui, React Flow |
| **Auth** | NextAuth v5 (Azure AD / Credentials) · Composer JWT (HS256) · per-user API keys |
| **Real-time** | WebSocket — node-by-node execution events |
| **Encryption at rest** | `cryptography` AES-256-GCM |
| **Sandboxing** | `simpleeval` (expressions) + `e2b_code_interpreter` (code) |
| **Tests** | pytest + pytest-asyncio (711 unit + integration), Playwright (frontend e2e) |
| **Tooling** | `uv` · `ruff` · `pyright` (strict) · Prisma migrations |

Why each piece was chosen, with alternatives considered: [`docs/decisions.md`](docs/decisions.md) (full ADR record).

---

## Documentation

| If you want to... | Read |
|---|---|
| **Understand what Composer is and why** | [`docs/overview.md`](docs/overview.md) |
| **Run it locally and build your first workflow** | [`docs/getting-started.md`](docs/getting-started.md) |
| **Build workflows on the canvas** | [`docs/designer-guide.md`](docs/designer-guide.md) — every node type with examples |
| **Operate Composer in production** | [`docs/operations.md`](docs/operations.md) + the runbooks under [`docs/operations/`](docs/operations/) |
| **Manage users / keys / models / MCP servers** | [`docs/admin-guide.md`](docs/admin-guide.md) |
| **Understand the internals** | [`docs/architecture.md`](docs/architecture.md) |
| **Call the HTTP API** | [`docs/api-reference.md`](docs/api-reference.md) |
| **Trace an engineering decision** | [`docs/decisions.md`](docs/decisions.md) — full ADR record |
| **Host Composer as a SaaS** | [`docs/saas/`](docs/saas/) — security, privacy, compliance, SLA, pricing, legal templates |
| **See what changed** | [`CHANGELOG.md`](CHANGELOG.md) |

### Hosting Composer as a SaaS

The [`docs/saas/`](docs/saas/) folder is the customer-facing layer for anyone running Composer for paying customers:

- [security](docs/saas/security.md) · [privacy](docs/saas/privacy.md) · [compliance](docs/saas/compliance.md) · [multi-tenancy](docs/saas/multi-tenancy.md)
- [SLA](docs/saas/sla.md) · [support](docs/saas/support.md) · [pricing](docs/saas/pricing.md) · [roadmap](docs/saas/roadmap.md)
- [customer onboarding journey](docs/saas/customer-onboarding.md)
- [legal templates](docs/saas/legal/) — Terms of Service, Privacy Policy, Acceptable Use, DPA, Sub-processors (review with counsel)

Operational runbooks under [`docs/operations/`](docs/operations/) cover [production deployment](docs/operations/production-deployment.md), [incident response](docs/operations/incident-response.md), [disaster recovery](docs/operations/disaster-recovery.md), [observability](docs/operations/observability.md), and [scaling](docs/operations/scaling.md).

---

## Testing

```bash
# Everything CI runs
uv run ruff check src tests && \
uv run ruff format --check src tests && \
uv run pyright src tests && \
uv run pytest -m "not integration"

# Frontend type-check
cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.json
```

**711 unit tests** + 1 integration test (gated by `@pytest.mark.integration`, hits a real Neon database). Frontend Playwright suite (gated by env). Pyright runs in **strict mode** with zero errors.

---

## Repository layout

```
composer/
├── src/                              # FastAPI backend
│   ├── main.py                       #   App entry + router wiring + lifespan
│   ├── api/                          #   REST + WebSocket endpoints (workflows, executions, run, uploads, admin, mcp_servers, ...)
│   ├── engine/                       #   LangGraph executor + workflow Pydantic models + event bus
│   ├── executors/                    #   18 node-type implementations
│   ├── llm/                          #   Provider dispatch (Anthropic / OpenAI / Google / Groq)
│   ├── mcp/                          #   MCP client + OAuth + base64-blob sanitiser
│   ├── tools/                        #   Built-in tool providers (Tavily, Firecrawl, ...)
│   ├── vectordb/                     #   5 vector DB providers with query + upsert
│   ├── security/                     #   Auth, JWT, encryption, rate limiting, password hashing
│   ├── storage/                      #   Prisma client lifecycle + LangGraph checkpointer
│   └── maintenance/                  #   Background tasks (stuck-execution sweeper)
├── frontend/                         # Next.js 14 app
│   ├── app/                          #   App Router pages: designer/, runs/, admin/, (auth)/
│   ├── components/composer/          #   Canvas, panels, role-aware nav
│   ├── lib/api/                      #   Typed REST clients (OpenAPI-generated schema)
│   └── e2e/                          #   Playwright suite
├── prisma/schema.prisma              # Single source of truth for the data model
├── tests/                            # pytest unit + integration
├── scripts/                          # Operational scripts (seed_templates, link checker, ...)
├── docs/                             # Documentation (see docs/README.md for the index)
└── .github/workflows/                # CI pipelines
```

A deeper map is in [`docs/architecture.md#repository-layout`](docs/architecture.md#repository-layout).

---

## Status

Composer is **production-ready** end-to-end:

- **Backend**: FastAPI + Postgres + LangGraph, all node types implemented + tested
- **Frontend**: Next.js 14 canvas + runs page + admin console, all role-aware audiences shipped
- **Auth**: standalone username/password, Azure AD SSO, embedded JWT (for IE-style integration), per-user API keys
- **Reference content**: 17 production-ready templates seed into every fresh deployment
- **Operations**: full runbook collection + DR procedures + incident-response playbook
- **SaaS readiness**: security / privacy / compliance / SLA / pricing / legal templates ready for customer review

What's on the road ahead: [`docs/saas/roadmap.md`](docs/saas/roadmap.md).

---

## License

MIT — see [`LICENSE`](LICENSE).
