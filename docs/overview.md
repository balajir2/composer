# Composer — Overview

**Composer is a visual workflow platform for building agentic AI applications.** Designers wire nodes together on a canvas — agents, HTTP calls, vector DB queries, branching logic, human approval gates — and the runtime executes them as LangGraph state machines with full observability and resumability.

## Status

Phases 0–10 complete. The platform is operational: backend, frontend canvas, admin console, end-user runs page, external invoke API, document upload, and a library of 17 reference templates. See [/CHANGELOG.md](../CHANGELOG.md) for the running record.

## What you can build

- **RAG pipelines** — ingest PDFs / DOCX / Markdown into a vector DB, query with retrieval-augmented agents
- **Research agents** — multi-source agents using Tavily search + Firecrawl scrape + structured extraction
- **Workflow automation** — classify-and-branch on customer support tickets, route via if-else, draft responses
- **Document processing** — upload a transcript, extract action items as structured JSON, draft a follow-up email
- **External-invoke APIs** — publish a workflow as a `POST /api/run/{slug}` endpoint with per-user API keys
- **Human-in-the-loop flows** — agent drafts content, reviewer approves or rejects, branched routing on the verdict

The 17 reference templates under **Designer → Templates** are the recommended starting point; each is annotated with what platform capability it demonstrates. See [`designer-guide.md#templates`](designer-guide.md#templates) for a walk-through.

## What's in the box

| Layer | Composer ships with |
|---|---|
| **18 node types** | start, end, agent, mcp, http, set-state, transform, data-transform, extract, if-else, while, user-approval, join-chunks, note, guardrails, gamma-ai, arcade, vector-db |
| **4 LLM providers** | Anthropic Claude, OpenAI, Google Gemini, Groq |
| **5 vector DBs** | Pinecone, Qdrant, Chroma, Weaviate, Milvus (query + upsert) |
| **6 built-in tool providers** | Tavily, Firecrawl, Serper, Browserless, Gamma, Arcade |
| **MCP support** | Static-auth MCP servers + OAuth-flow MCP servers (Highspot-grade) |
| **Auth modes** | Standalone JWT (local) + Embedded JWT (IE-compatible) + Azure AD SSO + per-user API keys |
| **Real-time** | WebSocket streaming of node-by-node execution events |
| **Observability** | LangSmith tracing on every workflow run + Vercel logs |

## Why Composer exists

Composer is a deliberate Python rebuild of [Open Agent Builder (OAB)](https://github.com/balajir2/open-agent-builder) — a TypeScript/Convex workflow platform — on a stack compatible with Bounteous's Intelligent Engineering platform: **FastAPI + Prisma Python + Postgres + LangGraph Python**.

Three governing rules from the project owner:

1. OAB is frozen — read-only behavioural reference, no parallel maintenance.
2. Composer is built fresh, not line-by-line ported. Internal architecture is free to be cleaner than OAB's.
3. Completion = OAB's regression suite passes against Composer.

The historical strategic context is preserved in [`archive/design-history/`](archive/design-history/) for anyone who wants the back-story. The day-to-day operational reality is what the rest of the docs describe.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11 / 3.12 |
| Web framework | FastAPI |
| ORM | Prisma Python |
| Database | Postgres 15+ (Neon for dev/prod, Docker for local) |
| Orchestration | LangGraph Python + LangChain |
| Frontend | Next.js 14 + Tailwind + shadcn/ui (`base-nova` style on `@base-ui/react`) |
| Auth | NextAuth v5 (Azure AD / Credentials) + Composer JWT (HS256) + per-user API keys |
| Real-time | WebSocket (Phase 9, matches IE `DES-007`) |
| Encryption | `cryptography` (AES-256-GCM) |
| Safe expressions | `simpleeval` (never `eval()`) |
| Code sandbox | `e2b_code_interpreter` |
| Package manager | uv |
| Lint / format | ruff |
| Type check | pyright (strict) |
| Tests | pytest + pytest-asyncio (~683 unit + 1 integration) |

Stack decisions and their rationale: [`decisions.md`](decisions.md). Each ADR records the alternatives considered and why the chosen path won.

## Where to next

- **Build a workflow** — [getting-started.md](getting-started.md) → [designer-guide.md](designer-guide.md)
- **Run Composer somewhere** — [operations.md](operations.md)
- **Add a new node type** — [architecture.md](architecture.md), then [`decisions.md`](decisions.md) for executor patterns
- **Integrate via API** — [api-reference.md](api-reference.md)
