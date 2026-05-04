# Operations

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)

Index of operational procedures. Detailed runbooks live under [`operations/`](operations/) — link directly to the runbook you need.

## End-to-end deployment

The single comprehensive checklist for going live, in order:

- **[`operations/production-deployment.md`](operations/production-deployment.md)** — Phases 0 through 10, from "deciding the region" to "handing over to operations." Read this first if you're standing up a new deployment.

The component-specific runbooks the deployment checklist depends on:

1. **[`operations/postgres-setup.md`](operations/postgres-setup.md)** — Provision Neon, set `DATABASE_URL`, run `prisma migrate deploy`.
2. **[`operations/llm-keys.md`](operations/llm-keys.md)** — Set LLM API keys in Postgres (the source of truth) and sync to your runtime env vars.
3. **[`operations/vercel-setup.md`](operations/vercel-setup.md)** — Connect the GitHub repo, paste env vars, deploy. (For non-Vercel hosts the env-var list is the same — only the deploy step differs.)
4. **[`operations/azure-sso.md`](operations/azure-sso.md)** *(optional, recommended for prod)* — Register Azure AD app, wire NextAuth, validate JWTs.
5. **[`operations/monitoring.md`](operations/monitoring.md)** — Turn on LangSmith tracing and Vercel log drains, set alerts.

A completed deployment passes all of these smoke tests:

- `GET /health` returns 200 with `{"status": "ok", ...}`
- A test user can register / sign in via SSO
- Admin can save an LLM API key via Admin → LLM keys and a designer can run a workflow that uses it
- An external invoke (`POST /api/run/{slug}` with `Bearer ck_...`) executes successfully
- The execution_sweeper boot log line appears (`execution_sweeper: started ...`)

## Production operations

- **[`operations/incident-response.md`](operations/incident-response.md)** — Detect → Acknowledge → Triage → Mitigate → Resolve → Postmortem. The runbook for "production is broken right now."
- **[`operations/disaster-recovery.md`](operations/disaster-recovery.md)** — Backups, restore procedures by failure class, the DR drill cadence.
- **[`operations/observability.md`](operations/observability.md)** — What to look at: logs, metrics, traces. The reference for "I see X, what does it mean?"
- **[`operations/scaling.md`](operations/scaling.md)** — Capacity model, signals that scaling is needed, what to scale first.
- **[`operations/admin-operations.md`](operations/admin-operations.md)** — Promote users to admin (SQL), run the OAB → Composer migration, reconcile users post-migration, reassign workflow / MCP server ownership.

## Where things live in production

| Concern | Source of truth | Synced to |
|---|---|---|
| Workflow definitions | Postgres `workflows` table | — |
| User accounts | Postgres `users` (standalone) or Azure AD (SSO) | Postgres User row created on first SSO login |
| LLM API keys | Postgres `llm_api_keys` (AES-256-GCM) | Vercel env vars (via `composer keys sync`) |
| Per-user API keys | Postgres `api_keys` (bcrypt-hashed) | — |
| MCP servers + OAuth tokens | Postgres `mcp_servers` + `mcp_oauth_tokens` (encrypted) | — |
| LLM model catalog | Postgres `llm_models` | Designer's model dropdown |
| Deployment settings | Postgres `deployment_settings` | Read by backend per-request |
| Execution checkpoints | Postgres `langgraph_checkpoints` | — |

## Stack quick reference

| Layer | What runs where |
|---|---|
| **Frontend** | Next.js 14 → Vercel Serverless Functions |
| **Backend HTTP** | FastAPI → containerised on Fly / Render / App Runner / your container host (Vercel Serverless can't keep WS connections open — see Vercel runbook) |
| **Database** | Postgres 15+ → Neon (managed) or self-hosted |
| **WebSockets** | Same FastAPI host (must be a long-lived process, not serverless) |
| **Tracing** | LangSmith (optional but recommended) |
| **Logs** | Vercel for frontend, your container host for backend |

## Required environment variables

The complete list is in `.env.example`. The non-negotiables for production:

```bash
DATABASE_URL=postgresql://...
JWT_SECRET=<32-byte hex>
ENCRYPTION_KEY=<32-byte hex>
ENVIRONMENT=production           # disables dev-mode auth fallback
COMPOSER_DEPLOYMENT_MODE=standalone   # or 'embedded' if running inside IE

# At least one LLM provider's key is required for any agent to run
ANTHROPIC_API_KEY=sk-ant-...
```

For Azure SSO add `AUTH_AZURE_AD_*` plus the NextAuth secret. For LangSmith tracing add `LANGCHAIN_*`. The runbooks above walk through each in context.

## Routine checks

| Check | Frequency | Where |
|---|---|---|
| `/health` reachable | Every minute (your uptime monitor) | Should return 200 |
| Auth flow end-to-end | Daily | Sign in, run a workflow, sign out |
| LangSmith trace count | Daily | `composer-production` project — if traces stop, tracing's broken |
| Postgres connection pool | Weekly | Neon dashboard — alert at 80% of plan |
| LLM key verification | Monthly | Admin → LLM models → click Verify on each row |

## Disaster recovery

- **Postgres**: Neon's PITR covers the last 7 days on the free tier (longer on paid). Branch from a point-in-time, switch `DATABASE_URL`, restart.
- **LLM keys**: Sourced from Postgres. Lose Postgres → re-encrypt and re-insert from your secret store of record. The keys never live only in env vars.
- **Per-user API keys**: Hashed; can't be recovered if Postgres is lost. Users regenerate via the Runs page.
- **Workflow definitions**: Postgres only. Lose Postgres → workflows are gone unless you've been exporting via `GET /workflows` periodically. Recommend doing so for production-published workflows at minimum.
