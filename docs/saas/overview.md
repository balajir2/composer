# Composer SaaS — Overview

> **Audience:** prospective customers, security reviewers, partners.
> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **For internal engineering audience:** see [`../overview.md`](../overview.md) and [`../architecture.md`](../architecture.md).
> **Commercial status:** proposed managed-service positioning. Only signed customer agreements
> create service, pricing, support, security, or availability commitments.

## What Composer is

Composer is a **visual workflow platform for agentic AI applications**. Designers wire nodes together on a canvas — agents, HTTP calls, vector DB queries, branching logic, human-approval gates, document upload — and the platform executes them as resumable state machines with full observability.

A single Composer deployment supports three audiences inside your organisation:

| Audience | What they do | Where they live |
|---|---|---|
| **Designers** | Author workflows on a React Flow canvas; pick LLMs, tools, MCP servers; publish workflows as `POST /api/run/{slug}` endpoints | `/designer/*` |
| **End users** | Run published workflows, watch live execution, approve human-in-the-loop steps, manage personal API keys | `/runs/*` |
| **Admins** | Manage users, LLM API keys, MCP servers, model catalog, deployment settings, global execution history | `/admin/*` |

What you can build with it: RAG pipelines, multi-source research agents, classify-and-branch ticket triage, document-intake-to-action-items flows, presentation generation, code review assistants, lead-enrichment pipelines, human-in-the-loop approvals. Composer ships with 19 reference templates that double as the recommended starting point.

## Proposed managed deployment package

Whether you self-host or we host for you, the contract is the same. Each Composer environment includes:

- **20 node types** (start, end, agent, mcp, http, set-state, transform, data-transform, extract, if-else, while, user-approval, join-chunks, note, guardrails, gamma-ai, email, arcade, vector-db, jira)
- **4 LLM providers** (Anthropic, OpenAI, Google, Groq) with per-model verification and auto-disable on retirement
- **5 vector DB connectors** (Pinecone, Qdrant, Chroma, Weaviate, Milvus) with query + upsert
- **6 built-in tool providers** (Tavily, Firecrawl, Serper, Browserless, Gamma, Arcade) plus full **MCP support** (static auth + OAuth flow, including Highspot-grade RFC 8707)
- **Three auth modes**: Standalone JWT (local accounts), Embedded JWT (drop into Bounteous IE), or Azure AD SSO via NextAuth
- **Per-user API keys** for external invokes
- **Real-time WebSocket streaming** of node-by-node execution events
- **LangSmith tracing** on every run
- **Document upload** with text extraction (PDF / DOCX / Markdown / TXT) — no persistence, no S3 dependency
- **19 reference templates** that demonstrate common patterns, so designers don't start from a blank canvas

## The deployment shapes we support

Composer is deployment-mode aware. The same code base runs in any of the topologies below; the choice is yours and changes only the auth wiring + a couple of env vars.

| Shape | Who manages what | Best for |
|---|---|---|
| **Customer self-host** | You run Composer in your own cloud (Postgres + a container host + optional CDN). We provide the code + runbooks. | Customers with an existing platform team and a strict data-residency requirement |
| **Composer-managed (single-tenant)** | We run a Composer instance dedicated to your organisation in a region of your choice. Your data never sits in shared infrastructure. | Mid-market and enterprise customers; the default for new deals today |
| **Embedded into Bounteous IE** | Composer slots in as a module of Bounteous's Intelligent Engineering platform. Auth, tenancy, and billing roll up to IE. | Customers who already have an IE relationship |

A future **shared-tenant** offering — many customer organisations in one Composer instance — is on the roadmap but not shipping yet. See [multi-tenancy.md](multi-tenancy.md) for the isolation analysis and migration path.

## How Composer is built (briefly)

Stack we're committed to (see [`../decisions.md`](../decisions.md) for the rationale on each):

| Layer | Choice | Why we picked it |
|---|---|---|
| Runtime | Python 3.11 / 3.12 | Aligns with IE; widest LangGraph + LangChain ecosystem |
| Web framework | FastAPI | Async-first; OpenAPI auto-generated; matches IE's transport pattern |
| ORM | Prisma Python | Type-safe schema-first ORM; same generator IE uses |
| Database | Postgres 15+ (Neon recommended) | Battle-tested transactional store; PITR available |
| Orchestration | LangGraph Python + LangChain | Resumable state machines with first-class checkpoint persistence |
| Frontend | Next.js 14 + Tailwind + shadcn/ui | Modern App Router; the canvas uses React Flow |
| Auth | NextAuth v5 + Composer JWT (HS256) + per-user API keys | Three-layer model — covers SSO, password, and machine-to-machine |
| Real-time | WebSocket | Long-lived connections for execution events; matches IE `DES-007` |
| Encryption at rest | `cryptography` AES-256-GCM | NIST-blessed; what we use to protect API keys + OAuth tokens |
| Sandboxed eval | `simpleeval` (never `eval()`) | The eval surface is restricted to sandboxed expression evaluation |
| Code execution sandbox | `e2b_code_interpreter` | When workflows need real Python execution |

## Why Composer exists

We built Composer because Open Agent Builder — the TypeScript/Convex predecessor — couldn't slot into Bounteous's Intelligent Engineering platform. The decision to rebuild instead of port was deliberate: the goal was Python on the IE stack from day one, with internal architecture free to be cleaner than OAB's. The completion bar is **behavioural parity** (OAB's regression suite must pass against Composer), not structural parity.

This matters for customers because:

1. **The lessons from running OAB in production are baked in.** The six MCP fixes from OAB's April 2026 Highspot incident, the execution-status truth defect class, the base64-blob sanitisation — all closed in Composer's day-one architecture. See [`../archive/incident-history/`](../archive/incident-history/) for the postmortems.
2. **Internal architecture is intentionally Python-idiomatic.** That gives us the flexibility to evolve quickly when a customer requirement lands without dragging behind a TypeScript-shaped legacy.
3. **Strategic continuity.** When IE incorporates Composer as a first-class module, the integration is a configuration change, not a rewrite.

## Where this doc points next

- **Building confidence to buy:** [security.md](security.md), [compliance.md](compliance.md), [privacy.md](privacy.md)
- **Building confidence to operate:** [sla.md](sla.md), [support.md](support.md), [`../operations/`](../operations/)
- **Understanding the trade-offs:** [multi-tenancy.md](multi-tenancy.md), [`../decisions.md`](../decisions.md)
- **Forecasting:** [roadmap.md](roadmap.md)
