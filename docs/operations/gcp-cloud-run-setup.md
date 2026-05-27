# GCP Cloud Run — Deployment Runbook

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Audience:** the engineer deploying Composer to Google Cloud Platform for the first time, plus anyone operating it day-to-day.
> **Project today:** `composer-497608` (region `us-central1`).

End-to-end recipe for running Composer on GCP. Cheapest possible setup that's still production-quality: Cloud Run (scale-to-zero default), Secret Manager for secrets, Artifact Registry for images, Neon for Postgres. ~$0–5/month at evaluation volume; ~$25–35/month for always-warm production.

## Architecture

```
                 ┌─────────────────────────────┐
                 │  Browser                    │
                 └────────────┬────────────────┘
                              │ HTTPS + WSS
              ┌───────────────┴───────────────┐
              │                               │
              ▼                               ▼
  ┌──────────────────────┐       ┌──────────────────────┐
  │  Cloud Run service   │       │  Cloud Run service   │
  │  composer-frontend   │       │  composer-backend    │
  │  Next.js 14          │       │  FastAPI + WS        │
  │  256–512 MiB / 1 vCPU│       │  1 GiB / 1 vCPU      │
  │  scale-to-zero       │       │  scale-to-zero,      │
  │                      │       │  CPU-always-allocated│
  └──────────────────────┘       └────────┬─────────────┘
                                          │
                                          ▼
                              ┌──────────────────────┐
                              │  Neon Postgres       │
                              │  (free tier)         │
                              └──────────────────────┘

  Secret Manager      Artifact Registry      GitHub Actions
  (DATABASE_URL,      (composer/backend,     (build + push +
   JWT_SECRET,         composer/frontend     deploy on push
   ENCRYPTION_KEY,     images, tagged        to main)
   LLM keys, etc.)     by SHA + latest)
```

## Prerequisites

Already done (per the GCP one-time setup you executed previously):

- [x] GCP project `composer-497608` exists with billing linked
- [x] APIs enabled: `run`, `artifactregistry`, `secretmanager`, `cloudbuild`, `iam`, `iamcredentials`, `logging`, `monitoring`
- [x] Artifact Registry repo `composer` in `us-central1`
- [x] Service account `composer-deployer@composer-497608.iam.gserviceaccount.com` with the four deploy roles
- [x] gcloud CLI installed + authenticated locally
- [x] Neon Postgres reachable via `DATABASE_URL` (the one you've been using locally)

Required to do this first deployment:

- [ ] Docker Desktop installed on your machine (for the first manual build) — verify with `docker --version`
- [ ] `.env` at the repo root populated with the secrets the bootstrap script will push (see the "Secrets" section below)
- [ ] Decision on a domain name (or skip and use `*.run.app` URLs)

## Step 1 — Populate `.env`

The bootstrap script reads from the repo-root `.env` and pushes those values to Secret Manager. The variables it looks for:

```bash
# Required
DATABASE_URL=postgresql://...                  # Neon connection string
JWT_SECRET=<openssl rand -hex 32>              # session signing
ENCRYPTION_KEY=<openssl rand -hex 32>          # secrets at rest (AES-256-GCM)
NEXTAUTH_SECRET=<openssl rand -hex 32>         # frontend session signing
ANTHROPIC_API_KEY=sk-ant-...                   # at least one LLM provider

# Optional — only push the ones you've signed up for
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
GROQ_API_KEY=gsk_...
TAVILY_API_KEY=tvly-...
FIRECRAWL_API_KEY=fc-...
LANGCHAIN_API_KEY=lsv2_pt_...
```

Anything missing in `.env` is skipped quietly (the script logs which were skipped). You can re-run the script to push more later.

**Note on `DATABASE_URL`** — Composer can use the *pooled* Neon URL for app traffic. If your Neon URL contains `channel_binding=require`, that's fine — Composer's Prisma client handles it.

## Step 2 — Run the first deploy

From the repo root:

```powershell
# Easiest — uses Cloud Run's auto-generated *.run.app URLs (no domain needed)
.\scripts\gcp-bootstrap.ps1

# Or with a custom domain (see Step 4 for the DNS records you'll need to add)
.\scripts\gcp-bootstrap.ps1 `
  -BackendDomain api.composer.example.com `
  -FrontendDomain composer.example.com
```

What it does (idempotent — safe to re-run any time):

1. Pushes every `.env` value to Secret Manager (creates new secrets the first time, adds a new version on re-run).
2. Builds + pushes the backend image to Artifact Registry.
3. Deploys `composer-backend` to Cloud Run.
4. Builds + pushes the frontend image with `NEXT_PUBLIC_COMPOSER_API_URL` baked in to point at the backend.
5. Deploys `composer-frontend` to Cloud Run.
6. Prints the two `*.run.app` URLs (or your custom domains).

First build takes ~3–5 minutes; subsequent re-runs are faster.

**Common flags:**

```powershell
.\scripts\gcp-bootstrap.ps1 -SkipSecrets  # only rebuild + redeploy; don't touch Secret Manager
.\scripts\gcp-bootstrap.ps1 -SkipBuild    # only redeploy the previously-built :latest images
```

## Step 3 — Smoke test

```powershell
$BackendUrl  = gcloud run services describe composer-backend  --region=us-central1 --format='value(status.url)'
$FrontendUrl = gcloud run services describe composer-frontend --region=us-central1 --format='value(status.url)'

curl "$BackendUrl/health"
# Expect: {"status":"ok","service":"composer",...}

start $FrontendUrl
# Browser opens; you can sign in with the credentials you registered locally
# (the DB is shared — your user already exists in Neon)
```

The very first request after a fresh deploy may take ~5–8 seconds while Cloud Run boots a new container instance. Subsequent requests are fast until the instance scales back to zero (~15 minutes of inactivity).

## Step 4 — Custom domain (optional but recommended)

Each Cloud Run service comes with a free `*.run.app` URL. Map a custom domain when you're ready:

### 4.1 — One-time domain verification

```powershell
gcloud domains verify your-domain.com
```

Browser opens; follow the prompt to add a TXT record to your domain's DNS. Once verified, every project under your Google account can map subdomains of that domain.

### 4.2 — Create the domain mappings

```powershell
gcloud run domain-mappings create `
  --service=composer-backend `
  --domain=api.composer.your-domain.com `
  --region=us-central1

gcloud run domain-mappings create `
  --service=composer-frontend `
  --domain=composer.your-domain.com `
  --region=us-central1
```

### 4.3 — Add DNS records

Both commands print CNAME records you need to add to your domain's DNS. Typically:

```
api.composer.your-domain.com   CNAME   ghs.googlehosted.com
composer.your-domain.com       CNAME   ghs.googlehosted.com
```

Add them, wait 1–10 minutes for propagation, then 15–60 minutes for Google to provision the TLS certificates.

### 4.4 — Re-deploy frontend with the new API URL

The frontend bakes `NEXT_PUBLIC_COMPOSER_API_URL` at build time. With a custom backend domain, re-run the bootstrap so the frontend image is built with the right URL:

```powershell
.\scripts\gcp-bootstrap.ps1 `
  -BackendDomain api.composer.your-domain.com `
  -FrontendDomain composer.your-domain.com `
  -SkipSecrets
```

Or set the `COMPOSER_API_URL` repository variable in GitHub (Settings → Variables → Actions) to `https://api.composer.your-domain.com` and push to `main` — GitHub Actions will rebuild + redeploy with the right URL.

## Step 5 — GitHub Actions CI/CD

After the first deploy works, hand subsequent deploys over to GitHub Actions. One-time setup:

### 5.1 — Create a JSON key for the deploy service account

```powershell
gcloud iam service-accounts keys create composer-deployer-key.json `
  --iam-account=composer-deployer@composer-497608.iam.gserviceaccount.com
```

This produces a JSON file in the current directory. **Treat it like a password** — don't commit it.

### 5.2 — Add it to GitHub

GitHub repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret name | Value |
|---|---|
| `GCP_SA_KEY` | the full contents of `composer-deployer-key.json` |

Add repository variables (same page, "Variables" tab):

| Variable name | Value | Purpose |
|---|---|---|
| `COMPOSER_API_URL` | `https://api.composer.your-domain.com` (or the backend `*.run.app` URL) | Baked into the frontend image as `NEXT_PUBLIC_COMPOSER_API_URL` |
| `COMPOSER_FRONTEND_URL` | `https://composer.your-domain.com` (or the frontend `*.run.app` URL) | Set as `NEXTAUTH_URL` on the frontend Cloud Run service |

### 5.3 — Delete the local key file

```powershell
Remove-Item composer-deployer-key.json
```

The key is now only in GitHub Secrets where it belongs.

### 5.4 — Push a commit

Any push to `main` triggers `.github/workflows/deploy-gcp.yml`. It:

1. Authenticates as `composer-deployer` using the JSON key.
2. Builds + pushes both images to Artifact Registry, tagged with the commit SHA + `latest`.
3. Deploys both Cloud Run services (frontend depends on backend so it picks up the right API URL).
4. Smoke-tests `/health` before declaring success.

You can also trigger manually via the Actions tab → Deploy to GCP Cloud Run → Run workflow.

## Step 6 — Going from scale-to-zero to always-warm

Default config is **scale-to-zero**: the container shuts down after ~15 minutes of inactivity, costs almost nothing, but first request after idle has a ~5–8s cold start.

For production traffic, flip to always-warm:

```powershell
gcloud run services update composer-backend `
  --region=us-central1 `
  --min-instances=1

gcloud run services update composer-frontend `
  --region=us-central1 `
  --min-instances=1
```

Each `min-instances=1` instance costs ~$20–25/month. Roll back any time with `--min-instances=0`.

Alternative: a free warmup ping (no min-instances cost). Add a cron job (e.g. [cron-job.org](https://cron-job.org)) that hits `/health` every 10 minutes. Cloud Run instances stay warm for ~15 minutes after the last request, so a 10-minute ping keeps one warm indefinitely. ~4,320 pings/month sits inside the free 2M-request Cloud Run quota.

## Operations

### Logs

```powershell
gcloud run services logs read composer-backend --region=us-central1 --limit=100
gcloud run services logs read composer-frontend --region=us-central1 --limit=100
```

Or via the Cloud Console: Cloud Run → service → Logs tab.

### Watch live

```powershell
gcloud run services logs tail composer-backend --region=us-central1
```

### Restart

```powershell
gcloud run services update composer-backend --region=us-central1 --no-traffic --tag=temp
gcloud run services update composer-backend --region=us-central1 --traffic=LATEST=100
```

Cloud Run doesn't have an explicit "restart" — pushing a new deploy is the equivalent. The above pattern forces a new revision without changing the image.

### Rollback

```powershell
# List recent revisions
gcloud run revisions list --service=composer-backend --region=us-central1

# Route 100% of traffic to a previous revision
gcloud run services update-traffic composer-backend `
  --region=us-central1 `
  --to-revisions=composer-backend-00007-xyz=100
```

### Rotate a secret

```powershell
# Push the new value
$new = "..."
$tmp = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($tmp, $new)
gcloud secrets versions add composer-jwt-secret --data-file=$tmp
Remove-Item $tmp

# Force the service to pick up the new version (Cloud Run pins `:latest`
# at deploy time)
gcloud run services update composer-backend `
  --region=us-central1 `
  --update-secrets=JWT_SECRET=composer-jwt-secret:latest
```

⚠️ **Rotating `JWT_SECRET` invalidates all active sessions** — users have to sign in again.

⚠️ **Rotating `ENCRYPTION_KEY` will make all encrypted secrets in Postgres unreadable** unless you migrate them first. Don't rotate this without a documented plan.

### Scale (cost) up or down

```powershell
gcloud run services update composer-backend --region=us-central1 `
  --memory=2Gi `        # if you see OOM
  --cpu=2 `             # if you see CPU saturation
  --concurrency=40 `    # if you see 503s under load
  --max-instances=10    # default is 3; raise if hitting the cap
```

The default settings (1 vCPU / 1 GiB / concurrency 80 / max-instances 3) are tuned for single-tenant single-user-at-a-time. Bump as needed.

### Verify scale-to-zero is still pinned

`min-instances=0` is set by [`scripts/gcp-bootstrap.ps1`](../../scripts/gcp-bootstrap.ps1) and preserved by the GitHub Actions deploy step (`gcloud run deploy --image=...` only swaps the image, keeping every other setting). To confirm:

```powershell
gcloud run services describe composer-backend  --region=us-central1 --format='yaml(spec.template.metadata.annotations)' | Select-String "minScale|maxScale"
gcloud run services describe composer-frontend --region=us-central1 --format='yaml(spec.template.metadata.annotations)' | Select-String "minScale|maxScale"
```

You should see only `maxScale: '3'`. The absence of `minScale` is intentional — Cloud Run defaults to zero, which is what we want for cost. Anyone running `gcloud run services update --min-instances=N` (or following Step 6) will add a `minScale` annotation, which is the signal to push back unless production warmup is wanted.

### Image cleanup (Artifact Registry)

A managed cleanup policy keeps the `composer` Artifact Registry repo bounded. It's checked in at [`scripts/artifact-registry-cleanup-policies.json`](../../scripts/artifact-registry-cleanup-policies.json) and runs daily on GCP's side — no CI step required.

Current rules (Keep beats Delete, so anything matched by a Keep rule survives age-based deletion):

| Rule | Action | Effect |
|---|---|---|
| `keep-latest-10-per-package` | Keep | Always retain the 10 most recent versions of `backend` and `frontend` |
| `keep-floating-tags` | Keep | Always retain anything tagged `latest` |
| `delete-untagged-after-7d` | Delete | Remove orphan layers (untagged digests from replaced builds) older than 7 days |
| `delete-tagged-after-90d` | Delete | Long-term cleanup of tagged historical images older than 3 months |

To update the policy, edit the JSON and re-apply:

```powershell
gcloud artifacts repositories set-cleanup-policies composer `
  --location=us-central1 `
  --policy=scripts/artifact-registry-cleanup-policies.json
```

To preview what *would* be deleted without actually removing anything:

```powershell
gcloud artifacts repositories update composer --location=us-central1 --cleanup-policy-dry-run
# (re-apply policy; deletions land in Cloud Logging under "artifactregistry.googleapis.com")
gcloud artifacts repositories update composer --location=us-central1 --no-cleanup-policy-dry-run  # flip dry-run off when satisfied
```

To list current images + storage:

```powershell
gcloud artifacts docker images list us-central1-docker.pkg.dev/composer-497608/composer --include-tags --sort-by=~UPDATE_TIME
gcloud artifacts repositories describe composer --location=us-central1 --format='value(sizeBytes)'
```

### Permissions

| Who needs it | What to grant |
|---|---|
| You (operator) | `roles/owner` on the project (default for the project creator) |
| GitHub Actions | `roles/run.admin`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser`, `roles/secretmanager.secretAccessor` (already granted to `composer-deployer`) |
| Read-only auditor | `roles/run.viewer` + `roles/logging.viewer` |
| Anyone running the bootstrap script | Project owner or the four roles above |

## Cost summary

Approximate monthly cost for the recommended config at single-tenant evaluation volume:

| Resource | Cost |
|---|---|
| Cloud Run backend (scale-to-zero, occasional traffic) | ~$0–3 |
| Cloud Run frontend (scale-to-zero) | ~$0–1 |
| Secret Manager (10 secrets) | ~$0.30 |
| Artifact Registry (bounded by cleanup policy — keep-10 + 90d) | ~$0.10–0.30 |
| Cloud Logging + Monitoring | $0 (under free tier) |
| Neon Postgres free tier | $0 |
| **Total** | **~$0–5/month** |

Going to always-warm (`min-instances=1` on both): add ~$25–35/month. Going to actually-busy production: see [`scaling.md`](scaling.md) for the cost knobs.

## Troubleshooting

### `gcloud run deploy` says "Permission denied"

Check who you're authenticated as: `gcloud auth list`. If running locally, your user account needs `roles/run.admin` on the project. If running from CI, confirm `GCP_SA_KEY` is set + the SA has the four deploy roles.

### Build succeeds but `/health` returns 500

Most common cause: a secret is missing or has the wrong value. Cloud Run logs show the FastAPI startup error. Check:
- `DATABASE_URL` points at a reachable Neon endpoint
- `JWT_SECRET` and `ENCRYPTION_KEY` are set
- The Neon endpoint isn't suspended (paste the URL into [https://console.neon.tech](https://console.neon.tech) and wake it)

### Frontend loads but calls fail with 404 / CORS / connection errors

The frontend image was built with the wrong `NEXT_PUBLIC_COMPOSER_API_URL`. Either re-run the bootstrap with `-BackendDomain` / `-FrontendDomain` matching your actual setup, or update the `COMPOSER_API_URL` GitHub variable and re-push.

### WebSocket disconnects mid-execution

Cloud Run caps individual WebSocket connections at 60 minutes (matching the `--timeout=3600` we set on deploy). For workflows longer than an hour, the frontend's DB-poll fallback recovers status from the persisted execution row.

### Cold start is slow

Set `--min-instances=1` (costs ~$25/month) or add a warmup ping (free — see Step 6).

### "execution_sweeper isn't running" alarm

The backend Cloud Run service must have `--no-cpu-throttling` (= "CPU always allocated") for the asyncio-based sweeper to tick between requests. The bootstrap script and Cloud Run deploy both set this. Verify with:

```powershell
gcloud run services describe composer-backend --region=us-central1 `
  --format='value(spec.template.spec.containers[0].resources.cpuIdleSpec)'
```

## Adjacent docs

- [`production-deployment.md`](production-deployment.md) — the host-agnostic Phase 0–10 checklist
- [`postgres-setup.md`](postgres-setup.md) — Neon provisioning (Step 1 above depends on it)
- [`monitoring.md`](monitoring.md) — alert + log-drain configuration
- [`incident-response.md`](incident-response.md) — what to do when something breaks at 2am
- [`disaster-recovery.md`](disaster-recovery.md) — backups, restore, RPO/RTO
- [`scaling.md`](scaling.md) — when to add capacity + how
- [`vercel-setup.md`](vercel-setup.md) — alternate deployment shape (Vercel frontend + separate backend host)
- [`../saas/multi-tenancy.md`](../saas/multi-tenancy.md) — single-tenant vs. shared-tenant trade-offs
