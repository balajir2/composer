# CLAUDE.md — Composer

**This file is auto-loaded by Claude Code. Read it in full before taking any action.**

---

## ⚠️ CRITICAL: Open Agent Builder repo access

The `D:/GitHub/open-agent-builder` directory on this machine contains Open Agent Builder (OAB). You have **READ-ONLY** access to it.

**Allowed:**
- `Read` files, `Glob`, `Grep` — for lookups and behavioral reference
- Browse the code to understand what a feature does
- Check test assertions (OAB's `tests/*.spec.ts`) to understand expected behavior
- Check OAB's git history (`git log`, `git blame`, `git show`) for historical context

**Strictly forbidden:**
- ❌ `Write`, `Edit`, `NotebookEdit` against any file in `D:/GitHub/open-agent-builder`
- ❌ `git add`, `git commit`, `git push`, `git checkout` (except read-only operations like `git log`), `git merge`, `git rebase`, `git reset` — any git command that modifies OAB's state
- ❌ Copy/pasting OAB code into Composer (build fresh, not port)
- ❌ Changing OAB's files even if you find bugs or think you can improve them

If a user asks you to modify OAB while working from the composer repo, refuse and remind them OAB is frozen. The only way to change OAB is to switch to an agent explicitly working in the OAB repo — and even then, the governing rule is "no changes, no fixes."

See §2 below for the full context on why OAB is frozen and how to use it correctly as reference.

---

## What Composer is

Python rebuild of [Open Agent Builder](https://github.com/balajir2/open-agent-builder) on an Intelligent Engineering (IE) compatible stack. Private repo at `balajir2/composer`.

**Goal:** When "should Composer become a module in IE?" becomes a real proposal, the code already exists on IE's stack, ready to plug in. This is a deliberate strategic response to David Lawton's (IE owner) 2026-04-20 critical analysis.

---

## Authoritative design — read this first

**[`docs/design/2026-04-20-composer-python-port-design.md`](docs/design/2026-04-20-composer-python-port-design.md)** is the active design. Before any non-trivial work, skim it. Every decision below is documented there with rationale.

Supporting docs in `docs/design/` provide historical context and product/strategic framing. The 2026-04-20 doc supersedes earlier 2026-04-15 ones where they conflict.

---

## ⚠️ Four governing rules (non-negotiable)

1. **Migration plan.** Work follows the 10-phase plan in the design doc (scaffolding → engine → LLM → MCP → executors → approval → more executors → API parity → security → cutover → UI). Don't skip phases.

2. **Open Agent Builder (OAB) is completely frozen.**
   - OAB lives at `D:/GitHub/open-agent-builder`
   - **Never `git commit` in OAB. Never.** Not even critical bug fixes.
   - OAB is read-only behavioral reference for Composer
   - If you find a bug in OAB while using it as reference, log it in Composer's `CHANGELOG.md` as "fixed in Composer"; do NOT fix in OAB

3. **Build fresh, not line-by-line port.**
   - Composer is written idiomatically in Python, not translated from TypeScript
   - Do NOT copy/paste OAB code
   - Do NOT mirror OAB's file structure if a cleaner Python structure exists
   - OAB source is behavioral reference only — read it to understand *what* a feature does, then design *how* in idiomatic Python

4. **Completion = OAB's regression suite passes against Composer.**
   - Objective behavioral parity, not structural parity
   - Internal architecture is free to be cleaner than OAB's
   - Port OAB's ~72 tests to pytest; those are the oracle

---

## Current phase status (as of 2026-04-20)

| Phase | Status | Notes |
|---|---|---|
| 0 — Scaffolding | ✅ Complete | Repo, FastAPI skeleton, `/health`, Prisma placeholder, CI green |
| 1 — Execution engine core | ✅ Complete | LangGraph Python executor, Start+End nodes, Postgres checkpointer, real Prisma schema |
| 2 — Agent + LLM providers | ✅ Complete | Agent executor + 10-iter loop, 4 LLM providers, Tool Provider Framework + 4 providers, MCP skeleton |
| 3a — MCP infrastructure (static auth) | ✅ Complete | DeepWiki + Firecrawl MCP verified end-to-end on `/mcp` streamable HTTP transport |
| 3b — MCP OAuth (Highspot + the six fixes) | ✅ Complete | All six OAB lessons encoded; real-Highspot MCP stack verified to auth boundary |
| 4a — Linear executors (http, set-state, transform, data-transform, extract) | ✅ Complete | 5 executors + simpleeval wrapper + recursive substitution; integration verified against real Anthropic |
| 4b — Control-flow (if-else, while) | ✅ Complete | add_conditional_edges; while cap 100; both if-else branches + while countdown verified against real Neon |
| 7a — Deployment-mode toggle + auth middleware | ✅ Complete | standalone vs embedded; User table; dev-mode fallback (ADR-0015); brought forward from Phase 7 |
| 5a — User-approval + interrupt/resume | ✅ Complete | `interrupt()` pause via `aget_state`; `/executions/{id}/resume`; `Approval` table; both branches verified against real Neon |
| 5b — SSE streaming | ✅ Complete | `GET /executions/{id}/events` with in-process asyncio bus; 5 event types; full taxonomy verified against real Neon |
| 6a — Note + Join-Chunks | ✅ Complete | note visual-only (skip lock-test from Phase 1); join-chunks concatenates chunk lists with separator/prefix/suffix/metadata; verified against real Neon |
| 6b — Guardrails | ✅ Complete | LLM-classifier (pii/moderation/jailbreak/hallucination); concurrent via asyncio.gather; verified against real Anthropic + Neon |
| 6c — Gamma-AI | ✅ Complete | HTTP integration with gamma.app; 60s/10s/4min polling; exportAs pptx/pdf supported; 13 unit tests, smoke test manual |
| 6d — Arcade | ✅ Complete | HTTP integration with auth-interrupt flow; reuses Phase 5a `/resume`; retry counter MAX_RETRIES=3; 14 unit tests, smoke test manual |
| 6e — Vector-DB | ✅ Complete | 5 providers (Pinecone/Qdrant/Chroma/Weaviate/Milvus) + OpenAI embeddings; provider framework per ADR-0020; ~30 unit tests, smoke test manual |
| 7 — API parity + regression suite ported | ⏭ Next | port OAB's ~72 pytest tests (objective parity check) |
| 8 — Security + hardening | ⏸ | |
| 9 — Cutover (Convex→Postgres migration, WebSocket) | ⏸ | |
| 10 — UI fork from OAB | ⏸ | Fork OAB's Next.js frontend, swap client layer |

Backend phases 0–9 ≈ 10 weeks. UI phase 10 ≈ 3 weeks. Total ≈ 13 weeks.

---

## Stack (locked — do not change without user approval)

| Layer | Choice |
|---|---|
| Language | Python 3.11 or 3.12 (`>=3.11,<3.13`) |
| Web framework | FastAPI |
| ORM | **Prisma Python** (user explicitly chose over SQLAlchemy for IE parity) |
| Database | Postgres 15+ (Neon for dev, Docker fallback) |
| Orchestration | LangGraph Python + LangChain |
| LLM providers | `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-groq` |
| Validation | Pydantic v2 |
| Auth | JWT (HS256) matching IE's `DES-004` pattern |
| Real-time | SSE (Phase 5) → WebSocket (Phase 9, matching IE `DES-007`) |
| Encryption | `cryptography` (AES-256-GCM) |
| Safe expressions | `simpleeval` (NEVER `eval()`) |
| Code sandbox | `e2b_code_interpreter` |
| Package manager | **uv** |
| Lint + format | **ruff** |
| Type check | **pyright strict mode** |
| Tests | **pytest** + pytest-asyncio |
| CI | GitHub Actions (`.github/workflows/ci.yml`) |

---

## Development commands

```bash
# Install / update dependencies
uv sync --all-extras

# Generate Prisma client after schema changes
uv run prisma generate

# Create a new migration (once Phase 1 adds tables)
uv run prisma migrate dev --name <migration_name>

# Run the dev server
uv run uvicorn src.main:app --reload

# Lint + format
uv run ruff check src tests
uv run ruff format src tests

# Type check (strict)
uv run pyright src tests

# Run tests
uv run pytest

# Run everything CI runs (before pushing)
uv run ruff check src tests && \
uv run ruff format --check src tests && \
uv run pyright src tests && \
uv run pytest
```

---

## 1. The six critical MCP fixes (from OAB's April 2026 debugging session)

Phase 3 MUST include all of these from day one. They are not optional — they are the hard-won lessons from a multi-day debugging session that made OAB's Highspot MCP integration work.

1. **RFC 8707 `resource` parameter** on OAuth authorize, token exchange, token refresh, AND client credentials grant. All four. Highspot rejects token exchange with "resource mismatch" if missing.
2. **Manual MCP tool calling.** Do NOT use Anthropic's native `mcp_servers` connector + `betas: ['mcp-client-2025-04-04']`. It fails on servers with large tool definitions (Highspot's tools/list exceeded 73K chars and triggered a JSON parse error). Fetch tools via `tools/list` JSON-RPC yourself and pass them as regular LangChain `Tool` objects.
3. **`inputSchema` (camelCase) support.** MCP spec uses `inputSchema`; OAB-era code used `input_schema`. Both schemas exist in the wild. Accept `inputSchema` first, fall back to `schema` and `input_schema`.
4. **Server-side OAuth token retrieval.** Tokens must NEVER transit through the client. When testing a connection or resolving servers for execution, the backend retrieves tokens from Postgres directly.
5. **Shared MCP servers (`isShared` flag) with service-account token fallback.** When a user can see a shared MCP but doesn't have their own OAuth token for it, fall back to the creator's token. Shared servers are discoverable by any user; OAuth tokens are per-server-record, not per-user-per-server.
6. **LangSmith config threaded explicitly.** Don't rely on `process.env` / env vars at runtime for LangSmith. Pass the config object through the execution path. OAB learned this when LangSmith tracing silently broke in production.

Reference: `docs/design/2026-04-15-composer-06-oab-knowledge-dump.md` §15 has detailed background on each.

---

## 2. Using OAB as reference (what's allowed, what's not)

OAB lives at `D:/GitHub/open-agent-builder`. See the **CRITICAL** callout at the top of this file — you have **read-only** access there. Below is the expanded rationale and examples.

### Why OAB is frozen

The user's four governing rules (above) include Rule 2: "Open Agent Builder continues as-is. No changes. No fixes. The repo is only available to the composer repo for lookup (read only)."

This is a deliberate strategic choice. OAB serves a handful of internal Bounteous users in its current TypeScript form. During the ~13-week Composer rebuild, OAB is NOT maintained in parallel. Bugs that would be introduced by changes in OAB (merge conflicts, broken tests, regressions) would tax the user's time and distract from Composer. The policy is: OAB stays exactly as it was on 2026-04-20, forever, until it is replaced by Composer.

### Allowed operations on OAB

```bash
# All of these are fine:
cat D:/GitHub/open-agent-builder/lib/workflow/langgraph.ts
grep -r "executeAgentNode" D:/GitHub/open-agent-builder/lib/
git -C D:/GitHub/open-agent-builder log --oneline -20
git -C D:/GitHub/open-agent-builder show 61dfb69 -- docs/
```

With Claude Code tools:
- `Read` a specific OAB file
- `Glob` for patterns in OAB's tree
- `Grep` OAB's code for symbols/references
- `Bash` with read-only commands (`cat`, `grep`, `git log`, `git show`, `git diff HEAD~1`)

### Forbidden operations on OAB

```bash
# All of these are VIOLATIONS of Rule 2:
echo "anything" > D:/GitHub/open-agent-builder/any-file.ts       # ❌
git -C D:/GitHub/open-agent-builder add .                        # ❌
git -C D:/GitHub/open-agent-builder commit -m "..."              # ❌
git -C D:/GitHub/open-agent-builder push                         # ❌
git -C D:/GitHub/open-agent-builder checkout -b feature/fix      # ❌
cp some-file D:/GitHub/open-agent-builder/lib/workflow/          # ❌ (writes to OAB)
```

With Claude Code tools:
- `Write`, `Edit`, `NotebookEdit` → the file path must NEVER start with `D:/GitHub/open-agent-builder/`
- `Bash` must not run any command that modifies OAB's filesystem or git state

### What to do if you find a bug in OAB during reference lookups

- Log it in Composer's `CHANGELOG.md` with a note like "Phase 3 — found bug in OAB's MCP resolver at lib/mcp/resolver.ts line N; fixed in Composer's `src/mcp/resolver.py`"
- Do NOT fix it in OAB
- Do NOT mention the bug to the user unless they ask — the frozen policy means the bug persists in OAB by design until Composer replaces OAB

### How to extract behavioral specs from OAB

Use OAB's tests as the definitive source of truth for "what this feature does":

```bash
# Find all tests touching a feature:
grep -r "Approval" D:/GitHub/open-agent-builder/tests/

# Read the full test to understand inputs, outputs, and edge cases:
cat D:/GitHub/open-agent-builder/tests/approval-flow.spec.ts
```

Then design Composer's Python implementation to produce behaviorally-equivalent results. Port the test to pytest when the feature is ready.

---

## 3. Conventions

### Commit messages

Every commit ends with these two co-author lines (both required):

```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

### Git discipline

- Commit messages explain *why*, not *what* (the diff shows what)
- One conceptual change per commit
- CI must be green before merge

### Type safety

- Pyright runs in **strict mode**
- All functions have typed parameters and return types
- FastAPI route handlers trigger `reportUnusedFunction` false positive — use `# pyright: ignore[reportUnusedFunction]` inline; see `src/main.py` for pattern (or refactor to `APIRouter` which pyright understands correctly)

### Tests

- Every executor, route, and integration gets tests
- Aim for >80% coverage per module (pytest-cov reports)
- Integration tests marked `@pytest.mark.integration` for ones that need external services

### Secrets / config

- No secrets in source files
- Local `.env` (gitignored) for dev
- `.env.example` documents the expected format
- Production config via deployment env vars (Phase 9)

---

## 4. Known gotchas from Phase 0

Record of issues hit during scaffolding — watch for these:

1. **Prisma refuses to `generate` with zero models.** Add at least one placeholder model if others aren't ready yet. See `prisma/schema.prisma`'s `ComposerBootstrap` — remove when Phase 1 adds real tables.
2. **Prisma disallows leading underscore in model names.** Use `@@map("snake_case_name")` if you want a specific underscore-prefixed table name.
3. **Ruff's UP035** — `AsyncIterator` must come from `collections.abc`, not `typing`.
4. **Pyright's `reportUnusedFunction`** on FastAPI `@app.get` handlers is a false positive. Suppress inline or use APIRouter pattern.
5. **LF/CRLF warnings in Git on Windows** — harmless. `.gitattributes` could be added if it becomes annoying.

---

## 5. When to ask the user vs. just do it

**Just do it (auto mode acceptable):**
- Implementing features per the phase plan
- Adding tests
- Fixing CI failures caused by your own changes
- Documentation updates
- Dependency additions for phases already in flight

**Ask the user first:**
- Adding a new dependency NOT in the design doc (e.g., replacing Prisma with SQLAlchemy)
- Changing phase order
- Touching the OAB repo (per Rule 2, answer is almost always "no")
- Changing the stack decisions listed above
- Anything that would affect the communication plan (i.e., external-visible actions)
- Actions with blast radius beyond composer repo (e.g., creating other GitHub repos, deploying to external infra)

---

## 6. Quick "getting started" for a new Claude agent session

1. Read this file (done if you're here)
2. Read `docs/design/2026-04-20-composer-python-port-design.md` — the active design spec
3. Run `git log --oneline -10` to see recent commits
4. Check current phase status (§Current phase status above)
5. Start on the next phase's deliverables

That's it. Everything you need is here or linked from here.

---

## Links

- **Composer repo:** `https://github.com/balajir2/composer` (private)
- **OAB repo (frozen reference):** `D:/GitHub/open-agent-builder` → `https://github.com/balajir2/open-agent-builder`
- **Original OAB design discussion lives in:** `open-agent-builder/docs/superpowers/specs/` (mirrored here in `composer/docs/design/`)
