# Composer

Python rebuild of [Open Agent Builder](https://github.com/balajir2/open-agent-builder) on an IE-compatible stack: FastAPI + Prisma Python + Postgres + LangGraph Python.

**Status:** Phase 0 complete — Phase 1 in progress.
**Design:** see [`docs/design/2026-04-20-composer-python-port-design.md`](docs/design/2026-04-20-composer-python-port-design.md). Phase specs under [`docs/superpowers/specs/`](docs/superpowers/specs/). Engineering decisions at [`docs/design/decisions.md`](docs/design/decisions.md).

---

## Why Composer exists

Open Agent Builder (OAB) is a visual, low-code workflow platform built in TypeScript with Next.js and Convex. Composer rebuilds OAB's backend on Python/FastAPI/Postgres to match the Intelligent Engineering (IE) platform's stack, so that when Composer-in-IE becomes a real proposal, the code is ready.

**Governing rules:**
1. OAB continues as-is (frozen, read-only reference)
2. Composer is a fresh rebuild, not a line-by-line port
3. Completion is when OAB's regression suite passes against Composer
4. Internal structure is free to be cleaner than OAB's

---

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| ORM | Prisma Python |
| Database | Postgres 15+ (Neon for dev) |
| Orchestration | LangGraph Python + LangChain |
| Auth | JWT (HS256) |
| Real-time | SSE (Phase 5) → WebSocket (Phase 9, matching IE's `DES-007`) |
| Tests | pytest |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Lint + format | ruff |
| Type check | pyright (strict) |

---

## Getting started

### Prerequisites

- Python 3.11 or 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended) or pip
- A [Neon](https://neon.tech) Postgres branch (the free tier is enough)

### Setup

```bash
# 1. Clone
git clone https://github.com/balajir2/composer.git
cd composer

# 2. Create virtual env and install deps
uv sync --all-extras

# 3. Copy env template
cp .env.example .env
# Edit .env: set DATABASE_URL to your Neon connection string

# 4. Generate Prisma client
uv run prisma generate

# 5. Run migrations (once schema has tables — Phase 1 onward)
# uv run prisma migrate deploy

# 6. Run the dev server
uv run uvicorn src.main:app --reload
```

Visit http://localhost:8000/health — should return `{"status":"ok"}`.
Visit http://localhost:8000/docs — FastAPI auto-generated API docs.

---

## Development commands

```bash
# Run the dev server
uv run uvicorn src.main:app --reload

# Lint + format
uv run ruff check src tests
uv run ruff format src tests

# Type check
uv run pyright src tests

# Run tests
uv run pytest

# Generate Prisma client after schema changes
uv run prisma generate

# Create a new migration
uv run prisma migrate dev --name <migration_name>
```

---

## Repository layout

```
composer/
├── src/
│   ├── main.py              # FastAPI entrypoint
│   ├── config.py            # Pydantic settings
│   ├── api/                 # REST routes (Phase 1+)
│   ├── engine/              # LangGraph executor (Phase 1)
│   ├── executors/           # Node-type executors (Phase 1-6)
│   ├── mcp/                 # MCP client + OAuth (Phase 3)
│   ├── storage/             # Prisma models + checkpointer (Phase 1)
│   ├── security/            # Encryption, expressions, SSRF (Phase 4+)
│   └── integrations/        # Tools + LangSmith (Phase 2+)
├── tests/                   # pytest
├── prisma/
│   └── schema.prisma        # Data model (grows phase by phase)
├── .github/workflows/ci.yml # Lint + typecheck + pytest on PR
├── pyproject.toml           # uv + dependencies + tool config
└── README.md                # this file
```

---

## License

MIT — see `LICENSE`.
