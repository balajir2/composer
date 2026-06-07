# Getting Started

This is the path from a fresh checkout to running your first workflow on the canvas.

## Prerequisites

- **Python 3.11 or 3.12** (Composer pins `>=3.11,<3.13`)
- **Node 20+** (for the frontend; `node --version`)
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** as the Python package manager
- **A Postgres database** — easiest is a free [Neon](https://neon.tech) branch; Docker Postgres works too
- **At least one LLM API key** — Anthropic / OpenAI / Google / Groq

You don't need MCP servers, vector DBs, or Firecrawl/Tavily keys to get started — they're optional per-template.

## 1. Clone and install

```bash
git clone https://github.com/balajir2/composer.git
cd composer

# Backend deps
uv sync --all-extras

# Frontend deps
cd frontend && npm install && cd ..
```

## 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in the **required** values:

```bash
# Postgres connection — Neon connection string from the console
DATABASE_URL=postgresql://user:pass@ep-xxx.region.aws.neon.tech/composer

# Auth — pick any 32-char hex string
JWT_SECRET=<run: openssl rand -hex 32>

# Encryption — pick any 32-char hex string
ENCRYPTION_KEY=<run: openssl rand -hex 32>

# At least one LLM provider
ANTHROPIC_API_KEY=sk-ant-...
# Or:
# OPENAI_API_KEY=sk-...
# GOOGLE_API_KEY=...
# GROQ_API_KEY=gsk_...
```

Optional but recommended:

```bash
# Let designers research the web (Template 2, 5, 17)
TAVILY_API_KEY=tvly-...

# Let designers scrape pages (Template 3, 7, 9, 12)
FIRECRAWL_API_KEY=fc-...

# LangSmith traces (every workflow run shows up)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_...
LANGCHAIN_PROJECT=composer-dev
```

The full list of supported variables is in `.env.example`. Each is documented inline.

## 3. Set up the database

```bash
# Generate the Prisma client (always after schema changes too)
uv run prisma generate

# Apply migrations
uv run prisma migrate deploy

# Seed the 18 reference templates (idempotent — safe to re-run)
uv run python -m scripts.seed_templates
```

If `prisma generate` fails with `spawn prisma-client-py ENOENT` on Windows, ensure `.venv/Scripts` is on your PATH for that shell:

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
uv run prisma generate
```

## 4. Run both servers

The fastest path on Windows is the dev launcher — it opens two PowerShell windows (backend + frontend), validates the backend port is free, and confirms `frontend\.env.local` points at it:

```powershell
.\scripts\dev.ps1                          # backend on 8001 (default), frontend on 3000
.\scripts\dev.ps1 -BackendPort 9000        # use a different backend port
```

Smoke test:

- http://localhost:8001/health → `{"status":"ok","service":"composer",...}`
- http://localhost:8001/docs → FastAPI auto-generated API docs
- http://localhost:3000 → Composer UI

If you'd rather start the servers manually (or you're not on Windows):

```bash
# Terminal 1 — backend
uv run uvicorn src.main:app --reload --port 8001

# Terminal 2 — frontend
cd frontend
npm run dev
```

In **dev mode** (no `ENVIRONMENT=production` env var) the backend allows an unauthenticated fallback user `dev` so you can start clicking around immediately. For real auth setup (Azure SSO or username/password), see [admin-guide.md](admin-guide.md).

> **Heads up: port collisions.** If `npm run dev` shows "Sign-in failed. Check your credentials." even with valid creds, the most likely cause is a different project squatting on the backend port. The launcher above checks for this; if you started uvicorn manually, verify with `curl http://localhost:8001/health` that it returns Composer's health response (`"service":"composer"`) and not some other app's. If a different app is on the port, switch backend + `frontend\.env.local` to a free port and restart `npm run dev` so the `NEXT_PUBLIC_*` env reload kicks in.

## 5. Build your first workflow

A complete walkthrough using the simplest template:

1. **Open the Templates gallery** at [http://localhost:3000/designer/templates](http://localhost:3000/designer/templates).
2. Find **"Example 1: Simple Agent"** and click **Use template**. You're now in the canvas of a fresh private copy.
3. **Inspect the agent node** — click it. The right-hand panel shows:
   - **Name**: `Answer Question` (this is also the alias `{{answer_question}}` for downstream references)
   - **Provider**: anthropic
   - **Model**: pick a model from the dropdown — only models the admin has enabled show up. If empty, see [admin-guide.md → LLM models](admin-guide.md#llm-models).
   - **Prompt**: `You are a helpful AI assistant. Provide a clear, concise answer to the following question:\n\n{{question}}\n\n…`
   - The `{{question}}` token is the input variable declared on the Start node — it gets replaced at run time with whatever the user provides.
4. **Click Save** in the top bar.
5. **Click Run Draft** (next to Save). A panel slides in from the right showing live execution events.
6. Enter a question (default: *What are the key benefits of using AI agents in workflow automation?*) and run.
7. Watch the canvas: the agent node pulses purple while running, turns green when complete. The right panel shows the answer.

That's the basic loop. From here:

- **Tweak the prompt** — edit the agent node's instructions, save, run again.
- **Add more inputs** — click the Start node, add a `tone` variable (type=text, default=`professional`). Reference it in the agent prompt as `{{tone}}`.
- **Try a more complex template** — Templates 8 (human approval), 11 (RAG), 14 (support triage) demonstrate more advanced patterns.

## 6. Publish a workflow as an API

To call your workflow from outside Composer:

1. Open the workflow's **Settings** (top bar).
2. Click **Publish**. Pick a URL-safe slug (e.g. `my-workflow`).
3. Generate an API key at [http://localhost:3000/runs/api-keys](http://localhost:3000/runs/api-keys) — copy the `ck_...` value (shown once).
4. Call the endpoint:

```bash
curl -X POST "http://localhost:8001/api/run/my-workflow" \
  -H "Authorization: Bearer ck_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"question": "What's new in agentic AI?"}, "sync": true}'
```

Async by default; pass `"sync": true` to wait for the result inline (capped at 300s per call). See [api-reference.md](api-reference.md) for the full external-invoke contract.

## Running tests

```bash
# Everything (matches CI)
uv run ruff check src tests && \
uv run ruff format --check src tests && \
uv run pyright src tests && \
uv run pytest -m "not integration"

# Just the unit suite (~2 minutes)
uv run pytest -m "not integration" -q

# Frontend type-check
cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.json
```

## Where to next

- **Build something real** — [designer-guide.md](designer-guide.md) covers every node type, variable substitution, templates, publishing, document uploads.
- **Understand the internals** — [architecture.md](architecture.md).
- **Deploy to staging / production** — [operations.md](operations.md).
- **Common admin tasks** — [admin-guide.md](admin-guide.md).
