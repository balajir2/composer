# Vercel Setup (frontend only — alternate path)

**Audience:** platform ops/SRE choosing to host the Next.js frontend on Vercel instead of Cloud Run.
Assumes a Vercel account exists and you have owner or admin access to the project.

> **This is not the recommended or currently-deployed path.** Composer's actual production
> deployment runs **both** the frontend and the backend on **GCP Cloud Run** — see
> [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md) and `.github/workflows/deploy-gcp.yml` for the
> live CI/CD pipeline. This document exists only for teams who prefer Vercel's frontend hosting;
> it does **not** describe deploying the FastAPI backend, which cannot run as a Vercel Serverless
> Function (see §5 below — no `vercel.json` exists in this repo, and none should be added for the
> backend). If you follow this path, the backend still needs a separate long-lived host (Cloud Run
> is recommended even in this mixed setup).

---

## Prerequisites

- Vercel account with access to the `balajir2` team (or the org that owns the deployment).
- GitHub repo `balajir2/composer` — Vercel must have GitHub integration enabled.
- The backend already deployed somewhere long-lived (Cloud Run recommended — see
  [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md)) and reachable over HTTPS + WSS.
- `NEXTAUTH_SECRET` ready to paste (32-byte hex — generate with `openssl rand -hex 32`).

---

## 1. Connect the GitHub repo to Vercel

1. Open [vercel.com/new](https://vercel.com/new).
2. Choose **Import Git Repository** → select `balajir2/composer`.
3. Composer is a monorepo; the Next.js app lives under `frontend/`. Set:
   - **Framework Preset:** Next.js
   - **Root Directory:** `frontend`
   - **Build Command:** _(default — `next build`)_
   - **Output Directory:** _(default)_
4. Click **Deploy**. The first deploy will likely fail because env vars are not set yet — that is
   expected. Continue to §2.

---

## 2. Set required environment variables

In the Vercel project → **Settings** → **Environment Variables**, add the following (matches
[`frontend/.env.example`](../../frontend/.env.example)). Set all to **Production** + **Preview**
unless noted.

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_COMPOSER_API_URL` | HTTPS URL of the deployed backend (e.g. the Cloud Run `composer-backend` URL) — used by the API client and the NextAuth Credentials provider |
| `NEXTAUTH_SECRET` | 32-byte hex string — generate with `openssl rand -hex 32`. Unique per environment. |
| `NEXTAUTH_URL` | The frontend's own public URL (e.g. `https://composer.example.com`) |
| `AZURE_AD_TENANT_ID` / `AZURE_AD_CLIENT_ID` / `AZURE_AD_CLIENT_SECRET` | Only if Azure SSO is enabled — see [azure-sso.md](azure-sso.md) |
| `NEXT_PUBLIC_AZURE_SSO_ENABLED` | `true` to show the "Continue with Azure" button on `/login`; `false` otherwise |

All backend-side concerns — `DATABASE_URL`, `JWT_SECRET`, `ENCRYPTION_KEY`, LLM provider keys,
LangSmith tracing — belong to the backend's own host, not this Vercel project. See
[gcp-cloud-run-setup.md](gcp-cloud-run-setup.md) (Secret Manager) or [llm-keys.md](llm-keys.md)
(Postgres source of truth) for those.

---

## 3. Domain setup

1. In the Vercel project → **Settings** → **Domains**, add your custom domain (e.g.
   `composer.example.com`).
2. Vercel provides the DNS records to configure (CNAME or A/AAAA). Add them in your DNS provider.
3. Vercel auto-provisions a TLS certificate via Let's Encrypt.
4. Propagation takes 1–10 minutes depending on TTL. Verify with:

```bash
curl -I https://composer.example.com/login
# Expected: HTTP/2 200
```

---

## 4. Triggering a deploy

Every push to `main` triggers an automatic production deploy (if the Vercel project is linked to
`main` as the production branch). To trigger manually:

```bash
npx vercel --prod
```

Or via the Vercel dashboard: **Deployments** → **Redeploy** on the latest commit.

---

## 5. Why the backend does not run on Vercel

`/executions/{id}/ws` is a long-lived WebSocket (DES-007). Vercel's default Serverless Function
model doesn't support WebSocket connections longer than the platform's idle timeout, so the
FastAPI backend needs a genuinely long-running host instead:

- **Cloud Run** (recommended, and what's actually deployed today) — see
  [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md). CPU-always-allocated keeps WebSocket
  connections alive.
- Any other long-lived container platform (Fly.io, Render, AWS App Runner, a VM) would also work,
  but isn't what CI/CD (`deploy-gcp.yml`) currently targets.

Do not add a `vercel.json` at the repo root to route the FastAPI app through Vercel's Python
runtime — that was an earlier idea explored before the WebSocket requirement landed, never
implemented, and would break real-time execution streaming.

---

## 6. Where logs go

- **Vercel runtime logs:** Vercel dashboard → **Deployments** → select a deployment → **Logs** tab.
  Streams frontend request logs in real time.
- **Log drain:** For persistent log storage (Datadog, Papertrail, Logtail, etc.), configure a drain
  in Vercel → **Settings** → **Log Drains**. See [monitoring.md §2](monitoring.md).
- Backend logs (execution traces, errors) live wherever the backend is hosted — see
  [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md) for Cloud Run's log viewer, and
  [monitoring.md §1](monitoring.md) for LangSmith tracing.

---

## Cross-references

- [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md) — the recommended, actually-deployed path for
  both frontend and backend
- [postgres-setup.md](postgres-setup.md) — Neon provisioning, `DATABASE_URL` format
- [llm-keys.md](llm-keys.md) — LLM key storage; `composer keys sync --target vercel` (a secondary
  key-distribution channel, independent of where the backend itself runs)
- [azure-sso.md](azure-sso.md) — Azure AD env vars referenced in §2
- [admin-operations.md](admin-operations.md) — post-deploy admin tasks
- [monitoring.md](monitoring.md) — log drain, LangSmith, error rate setup
