# Vercel Setup

**Audience:** Bounteous ops/SRE. Assumes a Vercel account exists and you have owner or admin access
to the project. Assumes you have already completed [postgres-setup.md](postgres-setup.md) and
[llm-keys.md](llm-keys.md), because required env vars come from both.

---

## Prerequisites

- Vercel account with access to the `balajir2` team (or the org that owns the deployment).
- GitHub repo `balajir2/composer` — Vercel must have GitHub integration enabled.
- Neon Postgres provisioned and migrated (see [postgres-setup.md](postgres-setup.md)).
- All LLM keys set in Postgres and synced to Vercel env vars (see [llm-keys.md](llm-keys.md)).
- `ENCRYPTION_KEY`, `JWT_SECRET`, and `DATABASE_URL` ready to paste.

---

## 1. Connect the GitHub repo to Vercel

1. Open [vercel.com/new](https://vercel.com/new).
2. Choose **Import Git Repository** → select `balajir2/composer`.
3. Vercel detects the project root. Composer is a FastAPI Python app — not a Next.js app. Set:
   - **Framework Preset:** Other
   - **Root Directory:** `.` (repo root)
   - **Build Command:** _(leave blank — Vercel uses the runtime adapter)_
   - **Output Directory:** _(leave blank)_
4. Click **Deploy** to complete the initial link. The first deploy will likely fail because env vars are
   not set yet — that is expected. Continue to §2.

> Vercel Python runtime executes `src/main.py` via the ASGI adapter. The `vercel.json` in the repo
> root configures the routing and runtime version.

---

## 2. Set required environment variables

In the Vercel project → **Settings** → **Environment Variables**, add the following. Set all to
**Production** + **Preview** environments unless noted.

### Core app

| Variable | Value |
|---|---|
| `APP_NAME` | `composer` |
| `ENVIRONMENT` | `production` |
| `LOG_LEVEL` | `INFO` |

### Database

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon connection string — see [postgres-setup.md §2](postgres-setup.md) |

`TEST_DATABASE_URL` is not needed on Vercel (tests don't run in the deployed environment).

### Auth

| Variable | Value |
|---|---|
| `JWT_SECRET` | Long random string — generate with `python -c "import secrets; print(secrets.token_hex(64))"` |
| `JWT_ALGORITHM` | `HS256` |
| `JWT_ACCESS_TTL_SECONDS` | `3600` |
| `JWT_REFRESH_TTL_SECONDS` | `604800` |

**JWT_SECRET must be unique per environment.** Use a different value for Production vs. Preview.
Rotating `JWT_SECRET` invalidates all existing sessions — notify users before doing this.

### Encryption

| Variable | Value |
|---|---|
| `ENCRYPTION_KEY` | 32-byte base64 key — generate with `python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"` |

`ENCRYPTION_KEY` must stay stable. Rotating it without re-encrypting stored MCP OAuth tokens and LLM
keys will render them unreadable. See [admin-operations.md §5](admin-operations.md) for key rotation
procedure.

### Deployment mode

| Variable | Value |
|---|---|
| `COMPOSER_DEPLOYMENT_MODE` | `standalone` |

Leave as `standalone` unless IE embedded mode is being deployed (Phase 10).

### LLM providers

LLM keys are managed via Postgres and synced here by `composer keys sync --target vercel`. Do **not**
set them manually in the Vercel UI unless you are doing a one-off emergency override. See
[llm-keys.md](llm-keys.md) for the full flow.

After running `composer keys sync`, Vercel shows:

```
ANTHROPIC_API_KEY
OPENAI_API_KEY
GOOGLE_API_KEY
GROQ_API_KEY
LANGCHAIN_API_KEY
TAVILY_API_KEY
FIRECRAWL_API_KEY
SERPER_API_KEY
BROWSERLESS_API_KEY
GAMMA_API_KEY
```

### LangSmith tracing (optional)

| Variable | Value |
|---|---|
| `LANGCHAIN_TRACING_V2` | `true` to enable; `false` to disable |
| `LANGCHAIN_PROJECT` | `composer-production` (or any project name in your LangSmith account) |
| `LANGCHAIN_ENDPOINT` | `https://api.smith.langchain.com` |

`LANGCHAIN_API_KEY` is synced via `composer keys sync` alongside the LLM keys.

### Deploy sync (needed only on the machine running `composer keys sync`, not on Vercel itself)

`VERCEL_API_TOKEN` and `VERCEL_PROJECT_ID` are **not** set in Vercel. They are used on the ops
machine to drive `composer keys sync`. See [llm-keys.md §3](llm-keys.md).

---

## 3. Domain setup

1. In the Vercel project → **Settings** → **Domains**, add your custom domain (e.g.
   `composer.bounteous.com`).
2. Vercel provides the DNS records to configure (CNAME or A/AAAA). Add them in your DNS provider.
3. Vercel auto-provisions a TLS certificate via Let's Encrypt.
4. Propagation takes 1–10 minutes depending on TTL. Verify with:

```bash
curl -I https://composer.bounteous.com/health
# Expected: HTTP/2 200
```

---

## 4. Triggering a deploy

Every push to `main` triggers an automatic production deploy (if the Vercel project is linked to
`main` as the production branch). To trigger manually:

```bash
# Using Vercel CLI
npx vercel --prod
```

Or via the Vercel dashboard: **Deployments** → **Redeploy** on the latest commit.

**After rotating LLM keys** (via `composer keys sync`), you must trigger a redeploy so Vercel picks
up the new env var values — Vercel injects env vars at build time, not dynamically at request time.

```bash
npx vercel --prod --force   # redeploy without code change
```

---

## 5. Rotating `VERCEL_API_TOKEN`

`VERCEL_API_TOKEN` is the token used by `composer keys sync` to push env vars to Vercel. It is stored
on the ops machine (in `.env` or CI secrets), not in Vercel itself.

Rotation procedure:

1. In Vercel → **Account Settings** → **Tokens**, create a new token with **Full Account** scope (or
   the minimum scope: `project:write` on the specific project).
2. Update `VERCEL_API_TOKEN` in the ops machine's `.env` file (or CI secret store).
3. Run a test sync to confirm the new token works:
   ```bash
   composer keys sync --target vercel
   ```
4. Delete the old token in Vercel's **Tokens** page.

Token rotation does not affect running deployments. It only affects the ability to push env vars.

---

## 6. Where logs go

- **Vercel runtime logs:** Vercel dashboard → **Deployments** → select a deployment → **Logs** tab.
  Streams stdout/stderr from all requests in real time.
- **Log drain:** For persistent log storage (Datadog, Papertrail, Logtail, etc.), configure a drain in
  Vercel → **Settings** → **Log Drains**. See [monitoring.md §2](monitoring.md) for recommended
  setup.
- **LangSmith traces:** If `LANGCHAIN_TRACING_V2=true`, all LangGraph execution traces appear in the
  LangSmith dashboard filtered by `LANGCHAIN_PROJECT`. See [monitoring.md §1](monitoring.md).

---

## 7. Health check

After deploy, verify the app is up:

```bash
curl https://composer.bounteous.com/health
# Expected:
# {"status": "ok", "version": "..."}
```

If the health check returns 500, check Vercel logs for startup errors — most commonly a missing env
var or a Postgres connection failure.

---

## Cross-references

- [postgres-setup.md](postgres-setup.md) — Neon provisioning, DATABASE_URL format
- [llm-keys.md](llm-keys.md) — LLM key storage and Vercel sync
- [admin-operations.md](admin-operations.md) — post-deploy admin tasks
- [monitoring.md](monitoring.md) — log drain, LangSmith, error rate setup
