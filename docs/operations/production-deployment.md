# Production Deployment Runbook

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Audience:** the engineer or platform team standing up Composer for production traffic.
> **Companion runbooks:** [postgres-setup.md](postgres-setup.md), [llm-keys.md](llm-keys.md), [vercel-setup.md](vercel-setup.md), [azure-sso.md](azure-sso.md), [monitoring.md](monitoring.md), [admin-operations.md](admin-operations.md), [incident-response.md](incident-response.md), [disaster-recovery.md](disaster-recovery.md), [observability.md](observability.md), [scaling.md](scaling.md).

This runbook is the **end-to-end checklist** for going live. It is not a replacement for the per-component runbooks above — those have the deep detail. This one keeps the order and the cross-component dependencies in one place so nothing gets skipped.

Read it in full once before starting. Each section ends with a **"Done when"** acceptance criterion you can pin against.

## Phase 0 — Decisions before infrastructure exists

Decisions you'll be locked into until the next major maintenance window. Make them deliberately.

| Decision | Options | Notes |
|---|---|---|
| Hosting region | EU / US / APAC | Anchored by data residency commitments to your customers. See [`../saas/privacy.md`](../saas/privacy.md). |
| Database host | Neon (recommended) / RDS / self-hosted Postgres | Neon's branching + PITR shorten DR drills considerably. |
| Backend host | Fly / Render / Cloud Run / your container platform | Must be **long-lived** for WebSocket support; not Vercel Serverless Functions. |
| Frontend host | Vercel (recommended) / any static + edge host | The frontend is Next.js 14 with App Router — Vercel's first-class. |
| Auth mode | Standalone (username/password) / Azure AD SSO / Embedded (IE-issued JWT) | SSO recommended for any customer-facing deployment. See [azure-sso.md](azure-sso.md). |
| LangSmith tracing | Enabled / disabled | Strongly recommended; opt out only if your privacy policy demands it. |
| Initial admin email | A real human's email | Not a shared inbox; promote a second admin on Day 1 ([../admin-guide.md](../admin-guide.md)). |
| Backup region | Same as primary / cross-region | Cross-region adds resilience at the cost of egress fees. |
| Encryption keys (`ENCRYPTION_KEY`, `JWT_SECRET`) | Generated per-deployment | Each is 32 bytes hex. They never leave the secret store after first save. |

**Done when**: every cell above has an answer recorded somewhere durable (a project-tracker ticket, a kickoff doc).

## Phase 1 — Provision infrastructure

### 1.1 Postgres database

Walk through [postgres-setup.md](postgres-setup.md). The end state:

- A Postgres 15+ database in your chosen region
- A connection string in your secret store under the name `DATABASE_URL`
- Point-in-time recovery enabled (Neon: default; RDS: explicitly enabled with retention ≥7 days)
- Daily backup job verified by checking the most recent backup timestamp in the host's UI
- Network ACL configured so only your backend host's egress IPs (or VPC) can reach the database

### 1.2 Backend host

Provision a container host that supports:

- Long-lived processes (no serverless function-duration cap)
- Outbound HTTPS to LLM providers + LangSmith + your DB
- Inbound HTTPS on a public hostname behind your CDN
- Environment-variable injection from the host's secret store
- Health-check probes against `/health`
- At least 1 vCPU + 1 GiB RAM per replica (more if you expect heavy concurrent execution)

Run a smoke test: `docker run` your built backend image with a minimal env configuration that points at the database, and curl `/health` from the host's network. It should return 200.

### 1.3 Frontend host

Walk through [vercel-setup.md](vercel-setup.md). The end state:

- Next.js project deployed to your Vercel team in your chosen region
- Production hostname configured (custom domain pointing at the Vercel deployment)
- Environment variables set: `NEXT_PUBLIC_COMPOSER_API_URL` pointing at your backend, `AUTH_SECRET` for NextAuth, etc.
- Build succeeded on the latest commit of your main branch

### 1.4 DNS + TLS

- Your customer-facing hostname (e.g., `composer.your-org.com`) points at the frontend host
- The backend hostname (e.g., `api.composer.your-org.com`) points at the backend host
- Both have valid TLS certificates (Vercel + most container hosts handle this automatically)
- HTTP requests redirect to HTTPS

**Done when**: `curl -I https://api.composer.your-org.com/health` returns 200 with a valid certificate chain, and `https://composer.your-org.com` loads the Composer login page.

## Phase 2 — Configure environment variables

Set the following on the backend host's environment. Anything marked **required** must be set; anything marked **recommended** has a sensible default but you almost always want to override it.

### Required — application

```bash
DATABASE_URL=postgresql://user:pass@host/composer  # 1.1 above
JWT_SECRET=<openssl rand -hex 32>                  # session signing
ENCRYPTION_KEY=<openssl rand -hex 32>              # secrets at rest (AES-256-GCM)
ENVIRONMENT=production                             # disables dev-mode auth fallback
COMPOSER_DEPLOYMENT_MODE=standalone                # or 'embedded' if running inside IE
```

### Required — at least one LLM provider

```bash
ANTHROPIC_API_KEY=sk-ant-...
# or any of: OPENAI_API_KEY, GOOGLE_API_KEY, GROQ_API_KEY
```

These can be added after deployment via the admin UI (Admin → LLM keys), but at least one must be set or admins can't run a smoke test.

### Recommended — SSO

```bash
AUTH_AZURE_AD_TENANT_ID=...
AUTH_AZURE_AD_CLIENT_ID=...
AUTH_AZURE_AD_CLIENT_SECRET=...
AUTH_AZURE_AD_EXPECTED_AUDIENCE=...
SSO_ENABLED=true
```

Per [azure-sso.md](azure-sso.md). Set even if you're rolling out SSO post-launch — turning it on is a settings change, not a redeploy.

### Recommended — observability

```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_...
LANGCHAIN_PROJECT=composer-production-${customer}
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

### Recommended — sweeper tuning

```bash
EXECUTION_STUCK_AFTER_SECONDS=900     # default 15min; tighten if your runtime has a shorter budget
EXECUTION_SWEEPER_INTERVAL_SECONDS=300 # default 5min; set to 0 to disable
```

### Recommended — rate limits (defaults are fine for most deployments)

```bash
RATE_LIMIT_EXECUTIONS_PER_MINUTE=30
RATE_LIMIT_API_RUN_PER_MINUTE=60
RATE_LIMIT_LOGIN_PER_MINUTE=10
```

The complete list of supported variables is in [`../../.env.example`](../../.env.example). Each is documented inline.

**Done when**: `/health` returns 200 and the response body shows the deployment mode, environment, and version you expect.

## Phase 3 — Run migrations + seed templates

Once the backend is reachable from where you can run management commands (locally with the `DATABASE_URL` exported, or via the host's exec/SSH):

```bash
# Generate the Prisma client (always after schema changes too)
uv run prisma generate

# Apply migrations
uv run prisma migrate deploy

# Seed the 17 reference templates (idempotent — safe to re-run)
uv run python -m scripts.seed_templates
```

If `prisma migrate deploy` fails because the database is read-only, the connection string is wrong — Neon often has a separate `POSTGRES_URL_NON_POOLING` for migrations vs. `POSTGRES_URL` for app traffic.

If template seeding fails on Windows (cp1252 encoding errors), run with `PYTHONIOENCODING=utf-8` set.

**Done when**: `SELECT count(*) FROM workflows WHERE is_template = true` returns 17 (or your customised template count).

## Phase 4 — First admin user

Standalone deployments:

1. Sign up via `https://composer.your-org.com/auth/register` with the admin's email and a strong temporary password.
2. Promote that user to admin via the SQL in [admin-operations.md](admin-operations.md). You don't have an admin yet, so this is a direct DB operation — that's expected.
3. Sign out and sign back in. The admin nav appears.
4. Rotate the password to something the human will remember.

Azure SSO deployments:

1. Sign in via `https://composer.your-org.com/auth/sso-callback` with the admin's Azure AD account.
2. The first SSO sign-in auto-creates the user with `member` role.
3. Promote via SQL as above.
4. Subsequent admins are promoted via the admin UI (Admin → Users → role toggle).

**Done when**: the admin can see Admin → Users, Admin → LLM keys, Admin → MCP servers in the left nav.

## Phase 5 — Configure LLM provider keys

Walk through [llm-keys.md](llm-keys.md) for each provider you intend to enable. For each:

1. Admin → LLM keys → Add → paste the key → save.
2. Click **Test** on the row to confirm the key works (issues a 1-token call).
3. Admin → LLM models → for each model the customer's workflows will use, click **Verify**. Models that fail Verify are auto-disabled and won't appear in designer dropdowns.

If you've also set the keys as backend env vars (Phase 2), the `composer keys sync --target vercel` CLI keeps the two in sync — see [llm-keys.md](llm-keys.md). Otherwise the Postgres-stored value is authoritative.

**Done when**: Admin → LLM models shows at least one verified model, with `verificationStatus=ok`.

## Phase 6 — Configure SSO (if not done in Phase 1)

Walk through [azure-sso.md](azure-sso.md). End state:

- Azure AD app registered with the right reply URLs
- `AUTH_AZURE_AD_*` env vars set on the backend
- A test sign-in from a non-admin Azure account succeeds and creates a `member` user
- The admin UI shows the new user

**Done when**: a clean-slate test user can sign in via SSO and reach the runs page.

## Phase 7 — Wire monitoring + alerting

Walk through [monitoring.md](monitoring.md). Minimum viable monitoring:

- **External uptime monitor** hitting `/health` every minute from at least three regions, with an alert to a paged channel on 3 consecutive failures.
- **Backend log shipping** to your aggregator (CloudWatch, Datadog, Loki, Vercel logs — whichever).
- **LangSmith trace count check** — if traces stop appearing for 30 minutes during expected traffic, paged alert.
- **Postgres connection pool alarm** at 80% of the plan limit.
- **Sweeper alarm** if the `composer.execution_sweeper` task isn't reporting heartbeat logs (you'll see "execution_sweeper: started" once at boot and "execution_sweeper: marked N/M ..." periodically).

**Done when**: you can see the health probe in the monitor, the backend logs in your aggregator, and a deliberately broken `/health` triggers the paged alert.

## Phase 8 — Production smoke test

Complete the canonical smoke test before declaring "we're live":

1. Sign in as a non-admin test user.
2. Open `/designer/templates` and clone "Example 1: Simple Agent".
3. Click Save → Run Draft → enter the default question → run. Watch the execution panel show:
   - `workflow_started` event
   - `node_started` for the agent node
   - `node_completed` with the agent's response
   - `workflow_completed`
4. Cross-check: `GET /executions/{id}` returns `status=completed` with the same output.
5. (LangSmith) Open the LangSmith project and confirm the trace appears.
6. As an admin, verify the run shows up in `/admin/workflows` (global feed).
7. Publish the workflow → generate an API key → call the external invoke:

```bash
curl -X POST "https://api.composer.your-org.com/api/run/example-1" \
  -H "Authorization: Bearer ck_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"question": "smoke test"}, "sync": true}'
```

The response should be a 200 with the agent's output, in under 30 seconds.

**Done when**: every step above passes on the first try.

## Phase 9 — Document the deployment

Update your internal documentation with:

- **Deployment URL** (frontend + backend)
- **Region**
- **Backend host platform** + the project / app id within it
- **Database** connection string (without the password) and host's project id
- **Vercel project id**
- **LangSmith project name**
- **Monitor configuration link**
- **The names of the two admin users** (primary + backup)
- **The runbook owner** (who responds to a Sev-1 paged alert)

This is what your incident-response team needs to know when the deployment misbehaves at 2 a.m. Without this, the first 30 minutes of every incident is just figuring out what's where.

**Done when**: a different engineer (one who didn't deploy it) can find every artefact above starting from the runbook entry alone.

## Phase 10 — Hand over to operations

If you're not the team that will operate the deployment:

- Walk the operations team through every section above
- Run a Sev-3 simulation: file a ticket, escalate it, confirm the response cadence works
- Run a partial DR drill: read [disaster-recovery.md](disaster-recovery.md), verify the team knows how to restore from backup
- Confirm the team has admin access (each person; not via shared credentials)

**Done when**: the operations team has a written sign-off that the deployment is theirs to operate, with clear escalation back to engineering for code-level issues.

## Going live

Once Phases 0–10 are complete, the deployment is ready for production traffic. Communicate the go-live date to the customer per the contract.

After go-live:

- **Hour 1**: watch the backend logs + LangSmith trace volume. Expect a small surge as the customer kicks tires.
- **Day 1**: review the execution success rate. Anything failing systematically? Investigate.
- **Week 1**: run the [disaster-recovery.md](disaster-recovery.md) full drill. You want to know the recovery path works before you need it.
- **Month 1**: run the [scaling.md](scaling.md) capacity review. Adjust the backend replica count + Postgres plan as needed.

## Rollback

If the deployment misbehaves materially:

| Failure mode | Rollback path |
|---|---|
| Backend won't start / crashes on boot | Roll back the container image to the previous tag; investigate offline. |
| Database migration applied something destructive | Restore from PITR to a pre-migration timestamp ([disaster-recovery.md](disaster-recovery.md)); fix the migration in code; re-apply. |
| Frontend renders white screen after deploy | Roll back the Vercel deployment to the previous one (one click in Vercel UI). |
| Auth misconfigured (nobody can sign in) | If you have backend SSH/exec, fix env vars + restart. If not, redeploy with corrected env. |
| Encryption key rotated incorrectly (existing secrets unreadable) | Restore database from backup, restore prior encryption key, retry rotation more carefully. **The key is more important than any single secret encrypted with it** — never rotate without a tested restore plan. |

The general principle: **prefer rollback over forward-fix during an incident**. Forward-fix lengthens the customer-impact window. Get back to the last-known-good state, investigate calmly, then re-deploy.

## Adjacent docs

- [postgres-setup.md](postgres-setup.md) — Phase 1.1
- [llm-keys.md](llm-keys.md) — Phase 5
- [vercel-setup.md](vercel-setup.md) — Phase 1.3
- [azure-sso.md](azure-sso.md) — Phase 1 / Phase 6
- [monitoring.md](monitoring.md) — Phase 7
- [admin-operations.md](admin-operations.md) — Phase 4 + ongoing
- [incident-response.md](incident-response.md) — what to do when go-live goes sideways
- [disaster-recovery.md](disaster-recovery.md) — backups + restore
- [observability.md](observability.md) — what to watch + how
- [scaling.md](scaling.md) — when to add capacity
- [`../saas/customer-onboarding.md`](../saas/customer-onboarding.md) — the customer-side timeline that pairs with this runbook
