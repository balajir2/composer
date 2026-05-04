# Composer

> Visual workflow platform for building agentic AI applications.
> Designers wire nodes on a canvas — agents, HTTP, vector DB, branching, human approval — and the runtime executes them as LangGraph state machines with full observability and resumability.

[![Phase](https://img.shields.io/badge/phase-10%20complete-success)](docs/overview.md#status)
[![Tests](https://img.shields.io/badge/tests-683%20passing-success)](#testing)
[![Stack](https://img.shields.io/badge/stack-FastAPI%20%7C%20Prisma%20%7C%20LangGraph%20%7C%20Next.js%2014-blueviolet)](docs/architecture.md)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## Why Composer

Composer is a Python rebuild of [Open Agent Builder](https://github.com/balajir2/open-agent-builder) on Bounteous's Intelligent Engineering (IE) compatible stack: **FastAPI + Prisma Python + Postgres + LangGraph Python**. The full why-and-how is in [`docs/overview.md`](docs/overview.md).

Three governing rules:

1. OAB is frozen — read-only behavioural reference.
2. Composer is built fresh, not ported line-by-line.
3. Completion is OAB's regression suite passing against Composer.

Phases 0–10 are complete. The platform is operational end-to-end.

---

## What you can build

- **RAG pipelines** — upload PDF/DOCX/MD → chunk → embed → upsert → retrieve + grounded answer
- **Research agents** — Tavily search + Firecrawl scrape + structured extraction
- **Workflow automation** — classify support tickets → if-else route → specialist replies
- **Document processing** — transcript → JSON action items → drafted follow-up email
- **Human-in-the-loop flows** — agent draft → reviewer gate → branched approve/reject paths
- **External-invoke APIs** — publish a workflow as `POST /api/run/{slug}` with per-user API keys

17 reference templates ship out of the box — see [`docs/designer-guide.md#templates`](docs/designer-guide.md#templates).

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
# Edit .env: DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, ANTHROPIC_API_KEY

# 3. Database + templates
uv run prisma generate
uv run prisma migrate deploy
uv run python -m scripts.seed_templates

# 4. Run
uv run uvicorn src.main:app --reload --port 8000   # backend
cd frontend && npm run dev                          # frontend (in another terminal)
```

Open http://localhost:3000 and click into the Templates gallery to start building. Full walkthrough: [`docs/getting-started.md`](docs/getting-started.md).

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11/3.12, FastAPI, Prisma Python, Postgres 15+ (Neon-compatible) |
| Orchestration | LangGraph Python + LangChain |
| LLM providers | Anthropic Claude · OpenAI · Google Gemini · Groq |
| Vector DBs | Pinecone · Qdrant · Chroma · Weaviate · Milvus (query + upsert) |
| Tool providers | Tavily · Firecrawl · Serper · Browserless · Gamma · Arcade · MCP (static + OAuth) |
| Frontend | Next.js 14 App Router, Tailwind, shadcn/ui (`base-nova`), React Flow |
| Auth | NextAuth v5 (Azure AD / Credentials) · Composer JWT (HS256) · per-user API keys |
| Real-time | WebSocket — node-by-node execution events |
| Tests | pytest + pytest-asyncio (683 unit, 1 integration), Playwright (frontend e2e) |
| Tooling | uv · ruff · pyright (strict) · Prisma migrations |

---

## Documentation

| If you want to... | Read |
|---|---|
| **Understand what Composer is and why** | [`docs/overview.md`](docs/overview.md) |
| **Run it locally and build your first workflow** | [`docs/getting-started.md`](docs/getting-started.md) |
| **Build workflows on the canvas** | [`docs/designer-guide.md`](docs/designer-guide.md) — all 18 node types, variables, templates, publishing |
| **Operate Composer in production** | [`docs/operations.md`](docs/operations.md) + [`docs/operations/`](docs/operations/) runbooks |
| **Manage users / keys / models / MCP servers** | [`docs/admin-guide.md`](docs/admin-guide.md) |
| **Understand the internals** | [`docs/architecture.md`](docs/architecture.md) |
| **Call the HTTP API** | [`docs/api-reference.md`](docs/api-reference.md) |
| **Trace an engineering decision** | [`docs/decisions.md`](docs/decisions.md) — full ADR record |
| **Host Composer as a SaaS — security, privacy, compliance, SLA, pricing** | [`docs/saas/`](docs/saas/) |
| **See what changed** | [`CHANGELOG.md`](CHANGELOG.md) |

### Hosting Composer as a SaaS

The [`docs/saas/`](docs/saas/) folder is the customer-facing layer for anyone hosting Composer for paying customers:

- [security](docs/saas/security.md), [privacy](docs/saas/privacy.md), [compliance](docs/saas/compliance.md), [multi-tenancy](docs/saas/multi-tenancy.md)
- [SLA](docs/saas/sla.md), [support](docs/saas/support.md), [pricing](docs/saas/pricing.md), [roadmap](docs/saas/roadmap.md)
- [customer onboarding journey](docs/saas/customer-onboarding.md)
- [legal templates](docs/saas/legal/) — Terms of Service, Privacy Policy, AUP, DPA, Sub-processors (review with counsel)

Operational runbooks that back these commitments live under [`docs/operations/`](docs/operations/) — including [production deployment](docs/operations/production-deployment.md), [incident response](docs/operations/incident-response.md), [disaster recovery](docs/operations/disaster-recovery.md), [observability](docs/operations/observability.md), and [scaling](docs/operations/scaling.md).

The `docs/archive/` folder holds the design-phase material (2026-04-15 brainstorming, IE critique, phase specs + plans, incident postmortems). Pristine for traceability, but you don't need it to understand or use the platform today.

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

683 unit tests, 1 integration test (real Neon, gated by `@pytest.mark.integration`). 11 Playwright tests across 5 specs (frontend e2e, gated by env).

---

## Repository layout

```
composer/
├── src/                    # FastAPI backend (api/, engine/, executors/, llm/, mcp/, vectordb/, …)
├── frontend/               # Next.js 14 app (designer, runs, admin)
├── prisma/schema.prisma    # Single source of truth for the data model
├── tests/                  # pytest unit + integration
├── scripts/                # Operational scripts (seed_templates.py, etc.)
├── docs/                   # Documentation — see docs/README.md for the index
└── .github/workflows/      # CI pipelines
```

A deeper map is in [`docs/architecture.md#repository-layout`](docs/architecture.md#repository-layout).

---

## License

MIT — see [`LICENSE`](LICENSE).
