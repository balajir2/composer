# Production Deployment Runbook

> **Maintained by:** Balaji Rajan (`balajirajan@gmail.com`)
> **Audience:** the engineer or platform team standing up Composer for production traffic.
> **Companion runbooks:** [postgres-setup.md](postgres-setup.md), [llm-keys.md](llm-keys.md), [vercel-setup.md](vercel-setup.md), [azure-sso.md](azure-sso.md), [monitoring.md](monitoring.md), [admin-operations.md](admin-operations.md), [incident-response.md](incident-response.md), [disaster-recovery.md](disaster-recovery.md), [observability.md](observability.md), [scaling.md](scaling.md).

This runbook is the **end-to-end checklist** for going live. It is not a replacement for the per-component runbooks above — those have the deep detail. This one keeps the order and the cross-component dependencies in one place so nothing gets skipped.

Read it in full once before starting. Each section ends with a **"Done when"** acceptance criterion you can pin against.

**Release ordering:** complete the database migration prerequisite in Phase 1.1a before starting the release's backend image, running its smoke test, or creating/enabling scheduler jobs that call it. Phase 3 verifies that prerequisite and seeds templates; do not postpone schema application until after the backend starts.

## Phase 0 — Decisions before infrastructure exists

Decisions you'll be locked into until the next major maintenance window. Make them deliberately.

| Decision | Options | Notes |
|---|---|---|
| Hosting region | EU / US / APAC | Anchored by data residency commitments to your customers. See [`../saas/privacy.md`](../saas/privacy.md). |
| Database host | Neon (recommended) / RDS / self-hosted Postgres | Neon's branching + PITR shorten DR drills considerably. |
| Backend host | Fly / Render / Cloud Run / your container platform | Must be **long-lived** for WebSocket support; not Vercel Serverless Functions. |
| Frontend host | GCP Cloud Run (current deployment); Vercel is supported as an alternate | The frontend is a Next.js 15 App Router server application, not a static-only export. |
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
- Point-in-time recovery enabled with a verified retention window that meets the deployment's recovery policy
- Daily backup job verified by checking the most recent backup timestamp in the host's UI
- Network ACL configured so only your backend host's egress IPs (or VPC) can reach the database

### 1.1a Apply release migrations before backend startup

**On GCP Cloud Run with the GitHub Actions pipeline, this happens automatically.** `.github/workflows/deploy-gcp.yml` runs `uv run composer checkpoints guard`, then `uv run alembic upgrade head`, then `uv run alembic current`, against the `composer-database-url` secret (direct, non-pooler endpoint) right before `gcloud run deploy`. Alembic is the only migration tool (Prisma was removed in SQLAlchemy migration phase 6; before that the workflow also ran `prisma migrate deploy`). If a migration fails, the deploy stops and the previous revision keeps serving. Since the old revision briefly runs against the new schema, migrations must be additive and backward-compatible. Run the steps below by hand for first-time setup, for other hosts, or to check a database.

Deploys are serialised: the workflow's `concurrency` group (`deploy-gcp-production`, no cancellation) makes a second push to `main` wait for the running deploy rather than run alongside it. If several pushes queue up, only the newest waiting run deploys, which is the latest `main`.

If `alembic upgrade head` fails, fix the cause and re-run the deploy job: the step is idempotent, and each revision runs in one transaction, so a failed revision leaves nothing half-applied. Rolling back a Cloud Run revision needs no database action (see [Rollback](#rollback)).

**The image does not contain `alembic.ini`** (the Dockerfile copies only `pyproject.toml`, `uv.lock`, `README.md` and `src/`), so the `alembic` CLI cannot run inside the container, for example through `gcloud run jobs execute` or a shell in the instance. Migrations run only from a repository checkout: the deploy workflow's checkout, or yours.

Use an administrative/release environment with the intended `DATABASE_URL` and the release's schema. This step does not need a running new backend:

```bash
# DATABASE_URL must be the DIRECT (non-pooler) host: migrations take locks that PgBouncer
# transaction pooling breaks. For Neon, remove "-pooler" from the hostname.
uv run composer checkpoints guard    # refuses (exit 1) while a paused run exists only in the old checkpoint tables
uv run alembic upgrade head
uv run alembic current               # must print the head revision
```

Alembic is the only migration tool. On an empty database, revision `0001_baseline` creates the base schema (the exact SQL of the 20 Prisma migrations that built every database before 2026-11-20), then the later revisions run in order, so a fresh database needs nothing else. A database that already has an `alembic_version` row is past `0001` and is never changed by it, so this step is a no-op on production until a new revision ships.

A database that only Prisma built (before 2026-11-20, no `alembic_version` table) is adopted by `alembic upgrade head` when its Prisma history is complete: nothing is re-run and Alembic records `0001_baseline`. Otherwise the upgrade refuses and names the missing or unknown migrations, or says that tables exist without any Prisma history. Finish such a database from a pre-phase-6 checkout (`git checkout 58d2829 && uv run prisma migrate deploy`) or restore it. `alembic downgrade` below `0001_baseline` is refused.

For this lifecycle release, verify the schema includes the former Prisma migration `20261117000000_execution_attempt_timing` before any new backend code, workers, or sweepers run (any database at or past `0001_baseline` does). It adds nullable `running_since` and `failure_code`; historical rows stay unchanged and new claims/resumes establish attempt timestamps. Inspect legacy running rows with null attempt timestamps separately because orphan sweeping requires `runningSince`. Validate real-Postgres integration races against the migrated schema before promotion.

If migration application or verification fails, keep the new backend revision stopped and its scheduler jobs uncreated/paused; resolve the database error before proceeding. On Cloud Run, this prerequisite comes **before** invoking `scripts/gcp-bootstrap.ps1`, since that script deploys the backend and creates scheduler jobs. Existing jobs must not target the new release until its schema is ready.

**Done when**: `uv run alembic current` prints the head revision of the release you are deploying (compare with `uv run alembic heads`), which includes the `running_since` and `failure_code` columns from `20261117000000_execution_attempt_timing`.

### 1.2 Backend host

Proceed only after Phase 1.1a succeeds. Provisioning the host can happen earlier, but starting the new application image or enabling its scheduler targets cannot.

Provision a container host that supports:

- Long-lived processes (no serverless function-duration cap)
- Outbound HTTPS to LLM providers + LangSmith + your DB
- Inbound HTTPS on a public hostname behind your CDN
- Environment-variable injection from the host's secret store
- Health-check probes against `/health`
- At least 1 vCPU + 1 GiB RAM per replica (more if you expect heavy concurrent execution)

Run a smoke test: `docker run` your built backend image with a minimal env configuration that points at the database, and curl `/health` from the host's network. It should return 200.

If deploying on GCP Cloud Run (the recommended path — see [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md)), this phase also provisions a Cloud Tasks queue and two Cloud Scheduler jobs (`composer-sweep` → `POST /internal/sweep`, `composer-poll-file-triggers` → `POST /internal/poll-file-triggers`), both authenticated via a shared OIDC service account — `scripts/gcp-bootstrap.ps1` handles all of this. This isn't required for local development or CI: `enqueue_execution()` falls back to in-process `asyncio` dispatch whenever `CLOUD_TASKS_SERVICE_ACCOUNT` is unset, so a developer can run a workflow end-to-end with zero Cloud Tasks/Scheduler infra provisioned.

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
EXECUTION_MAX_RUNTIME_SECONDS=1740    # active attempt: positive; default 29 minutes
EXECUTION_SHUTDOWN_GRACE_SECONDS=30   # 1–60 seconds; runtime + grace must be <= 1800
ORPHANED_RUNNING_GRACE_SECONDS=120    # positive lease-less claim grace, measured from runningSince
APPROVAL_WAIT_TIMEOUT_HOURS=0         # disabled; positive hours opt into deployment-wide expiration
EXECUTION_SWEEPER_INTERVAL_SECONDS=300 # default 5min in-process fallback loop; set to 0 on Cloud Run
```

The three timing settings above separate active processing from durable lifetime. Runtime must be positive; shutdown grace must be 1–60 seconds; their sum must not exceed 1800 seconds. Both the Cloud Tasks dispatch deadline and the processing lease derive from that sum (1770 seconds by default). Orphan grace must be positive and applies only to attempts with no lease, using `runningSince`, never the months-old execution's `startedAt`. A valid live lease takes precedence over execution age. Approval resumes start a fresh attempt; approval waits hold no live compute and may last months when retention permits.

On Cloud Run, set `EXECUTION_SWEEPER_INTERVAL_SECONDS=0` and let the `composer-sweep` Cloud Scheduler job (provisioned in Phase 1.2, `POST /internal/sweep` every 5 minutes) do the sweeping instead — the in-process loop requires `--no-cpu-throttling`, which is no longer part of the deploy config (see [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md)). The in-process loop only stays useful for local development or a non-Cloud-Run host without a scheduler equivalent.

`POST /internal/sweep` reports successful conditional failures as `orphaned_running` and `leases_expired`, alongside `approvals_expired` and `events_deleted`; a failed sweep component returns `null`. The response names describe terminal failure counts. **Automatic lease replay has been removed**: an expired attempt fails with `lease_expired`, and an orphan fails with `orphaned_running`, without enqueueing work that could repeat external side effects. Inspect failures before explicitly starting any replacement execution. Cooperative cancellation is terminal; already-started external operations may finish, and ordinary approval resume rejects canceled executions. Durable checkpoint/write/state data remains until explicit deletion or its retention policy removes it.

Approval expiration is disabled by default (`APPROVAL_WAIT_TIMEOUT_HOURS=0`). A positive value opts the entire deployment into approval expiry; it is independent of the emailed link's 72-hour default TTL and of the 29-minute processing budget.

### Recommended — rate limits (defaults are fine for most deployments)

```bash
RATE_LIMIT_EXECUTIONS_PER_MINUTE=30
RATE_LIMIT_API_RUN_PER_MINUTE=60
RATE_LIMIT_LOGIN_PER_MINUTE=10
```

The complete list of supported variables is in [`../../.env.example`](../../.env.example). Each is documented inline.

**Done when**: `/health` returns 200 and the response body shows the deployment mode, environment, and version you expect.

## Phase 3 — Run migrations + seed templates

Migration application was a hard prerequisite in Phase 1.1a, before backend startup and scheduler enablement. This phase verifies the already-applied schema and seeds templates; if the schema check fails, stop the new backend and pause its scheduler targets, then return to Phase 1.1a.

```bash
# Confirm the prerequisite completed against the intended database (direct, non-pooler URL)
uv run alembic current    # must print the head revision (compare with `uv run alembic heads`)

# Seed the 21 reference templates (idempotent — safe to re-run)
uv run python -m scripts.seed_templates
```

If `alembic upgrade head` fails because the database is read-only or hangs on a lock, the connection string is wrong — use the direct (non-pooler) URL for migrations; Neon often has a separate `POSTGRES_URL_NON_POOLING` for migrations vs. `POSTGRES_URL` for app traffic.

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
- **Sweeper alarm**: on Cloud Run, alert on the `composer-sweep` Cloud Scheduler job's failure rate (Scheduler → job → executions) rather than a boot log — the sweeper runs as a real inbound request (`POST /internal/sweep`) on a fixed interval, not an in-process loop. On a non-Cloud-Run host still running the in-process fallback (`EXECUTION_SWEEPER_INTERVAL_SECONDS > 0`), watch for "execution_sweeper: started" once at boot and "execution_sweeper: marked N/M ..." periodically instead.

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

The GCP backend rollout includes the OTLP collector as a Cloud Run sidecar. Keep its receivers bound to instance loopback and use Cloud Run ADC with only `roles/telemetry.writer` and `roles/serviceusage.serviceUsageConsumer` for telemetry export. Collector health is diagnostic; do not make backend startup depend on it. See [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md#otlp-collector-and-iam) for setup and failure behavior.

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

## Checkpointer cutover (phase 5 to phase 7)

SQLAlchemy phase 5 replaced the Prisma LangGraph saver with the official `langgraph-checkpoint-postgres` saver, which reads and writes new tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`; Alembic revision `0004_langgraph_saver_tables`). The old tables (`langgraph_checkpoints`, `langgraph_checkpoint_writes`) are kept until phase 7 so a rollback is possible. A run paused on the old saver cannot resume on the new revision unless its snapshots exist in the new tables.

### What the deploy guard checks

The deploy workflow runs `uv run composer checkpoints guard` BEFORE `alembic upgrade head`, and before the Cloud Run deploy, so a refusal leaves the database untouched. On a database that has not yet reached revision 0004 the new saver tables do not exist; the guard then treats them as empty, so every active run with old-table checkpoints is blocked. It fails the job (so the previous revision keeps serving) while any `queued`, `running` or `waiting_approval` run has checkpoints only in the old tables: the old tables' newest checkpoint id for its thread is newer than the new tables', or the new tables have none. It prints `active runs: <status>=<n> ...`, then either `ok: ...` (exit 0) or one `blocked: <execution id> (<status>)` line per run (exit 1). A failure of the check itself (for example no database) also exits 1: the guard fails closed. Output carries ids, statuses and counts only.

### Clearing the guard

1. Copy the paused runs forward: `uv run composer checkpoints copy --to postgres --all-waiting` (or `--execution <id>`, repeatable). The `copy` commands need the DIRECT (non-pooler) `DATABASE_URL`, the one the deploy uses:

   ```bash
   export DATABASE_URL="$(gcloud secrets versions access latest \
     --secret=composer-database-url --project=<PROJECT> | sed 's/-pooler\././')"
   ```
   (Run `copy --to postgres` only once the database is at revision 0004, i.e. after a deploy that applied the migration; before that, the guard's refusal is expected and the runs should finish or be cancelled.)
2. Runs still `running`/`queued` cannot be copied (only `waiting_approval` runs are, unless `--force`): wait for them to finish or pause, then copy; or cancel them.
3. Re-run the deploy.

Copy exit codes: 0 all copied; 1 a copy failed or copied nothing; 2 some run was refused (unknown execution, wrong status, or the target is already ahead of the source) while the others were still copied. Exit 2 is an expected outcome, not an error: read the `refused` lines. A copy that reports `nothing to copy` for a run (it has no checkpoints in the source) is harmless. If it happens during `--to legacy --all-waiting`, the run has no official checkpoints, so there is nothing to roll back for it, and the command exits 1.

Safety net: when a run is resumed, the executor first copies its thread from the old tables to the new ones if the old ones are ahead (`bring_forward`). A run that paused on the old revision after the guard ran (the window while Cloud Run shifts traffic) therefore still resumes. Roll-forward needs no manual step.

### Failure codes after the cutover

- `checkpoint_missing`: a resume found no saved state for the run. The run fails closed instead of restarting from scratch.
- `checkpoint_incompatible`: the workflow was edited while the run was paused, and the edit changed which paths a Join waits for, so the run's saved Join progress cannot be carried over. The run fails closed (it is not completed) and the user is told to start a new run; there is nothing to recover. Reordering edges is not such a change.
- `checkpoint_sync_failed`: copying the run's state forward (or reading it) failed during a resume. The run was not restarted and the old-table rows are intact, so it is recoverable: fix the cause (database reachability, a stale lock), confirm with `composer checkpoints guard` that the run is listed as blocked or copy it with `copy --to postgres --execution <id> --force`, then resume or re-queue it.

### Rollback runbook

Rolling back means the previous revision (which reads only the old tables) must find every paused run's state there. Placeholders: `<REGION>`, `<PROJECT>`, `<BACKEND_SERVICE>` (the Cloud Run service name), `<previous-rev>` (the revision name from `gcloud run revisions list --service=<BACKEND_SERVICE> --region=<REGION> --project=<PROJECT>`). The queue is `composer-executions` (`scripts/gcp-bootstrap.ps1`, `CLOUD_TASKS_QUEUE` default in `src/config.py`). Do it in this order:

1. Pause the Cloud Tasks queue:
   ```bash
   gcloud tasks queues pause composer-executions --location=<REGION> --project=<PROJECT>
   ```
2. Copy state back, with the direct (non-pooler) `DATABASE_URL` (same `sed` as the deploy):
   ```bash
   export DATABASE_URL="$(gcloud secrets versions access latest \
     --secret=composer-database-url --project=<PROJECT> | sed 's/-pooler\././')"
   uv run composer checkpoints copy --to legacy --all-waiting
   ```
3. Shift traffic to the previous revision:
   ```bash
   gcloud run services update-traffic <BACKEND_SERVICE> \
     --to-revisions=<previous-rev>=100 --region=<REGION> --project=<PROJECT>
   ```
4. Wait for the new revision's instances to drain (its in-flight requests finish).
5. Copy again (runs that paused during the drain exist only in the new tables):
   ```bash
   uv run composer checkpoints copy --to legacy --all-waiting
   ```
6. Resume the queue:
   ```bash
   gcloud tasks queues resume composer-executions --location=<REGION> --project=<PROJECT>
   ```

The startup probe on the live service is the default TCP probe on port 8080, not `/ready`. An old revision therefore still starts and serves on a database already at 0004; only its `/ready` reports a schema mismatch, which is expected during a rollback. Do NOT switch the startup probe to `/ready` while a rollback or a mixed-version window is possible: the old revision would fail its probe and never take traffic.

Risk: a run resumed on the old revision without its checkpoint in the old tables does not fail. The old revision finds no checkpoint and restarts the graph from scratch with empty input, repeating side effects (emails, HTTP calls, LLM calls). That is why the queue is paused first and the copy runs twice.

Known gap: during a traffic shift, a run that pauses on the new revision and is resumed on a still-draining old instance is not copied back, so the old saver restarts it from scratch. The window is small; pausing the queue (step 1) is what keeps it small.

Startup budget: the SQLAlchemy connect retry and the saver pool open retry run in series at boot, about 21 s worst case with the defaults. The saver schema check is bounded by `CHECKPOINTER_POOL_TIMEOUT_SECONDS` (10 s), and every database-facing startup step (both of those, the listener setup and the LLM key sync) runs under one deadline, `STARTUP_TIMEOUT_SECONDS` (60 s), after which startup raises and Cloud Run starts another instance; cancelling a query on a connection that stopped answering adds up to about 10 s. Keep the deadline well under the 240 s TCP startup probe, and keep it above the retry budget, before tightening any startup probe or timeout. Every connection also carries libpq `connect_timeout`, TCP keepalives and `tcp_user_timeout` (`src/db/libpq.py`), so a silent peer cannot hold a connection for the kernel's ~15 minute retransmission timeout.

### Notifications and connections

- LISTEN for live WebSocket updates uses one lazy connection per process on the direct (non-pooler) Neon endpoint. `DATABASE_DIRECT_URL` is optional; when unset it is derived from `DATABASE_URL` by removing `-pooler` from the host's first label (what the deploy workflow's `sed` does). The connection opens when the first WebSocket watches and closes `EVENT_LISTENER_IDLE_CLOSE_SECONDS` (default 30) after the last one leaves, so it never keeps Neon compute awake. NOTIFY goes through the normal pool.
- Before phase 5, LISTEN went through whatever `DATABASE_URL` pointed at, which in production is the pooler host; Neon documents that LISTEN does not work through its PgBouncer transaction-mode pooler, so live updates probably fell back to the 15 s poll (not confirmed against production). After phase 5 they should arrive within seconds; verify on a run page after the first deploy.
- Saver pool: its own `psycopg_pool` (`CHECKPOINTER_POOL_MIN_SIZE`=1, `CHECKPOINTER_POOL_MAX_SIZE`=5, `CHECKPOINTER_POOL_TIMEOUT_SECONDS`=10) on the pooler host with `prepare_threshold=None`. Budget per instance: SQLAlchemy 5+5, saver 5, so at most 15 pooler connections (45 at 3 instances) and 1 direct connection (3 at 3 instances), plus one-shot direct connections during deploys. Escape hatch: `CHECKPOINTER_USE_DIRECT_ENDPOINT=true` points the saver pool at the direct endpoint without a code change.
- Never run `AsyncPostgresSaver.setup()`; the saver's tables are Alembic revision 0004, and the app checks the saver schema version at startup.
- Never point a 2 s Cloud Run startup probe at `/ready`. Occasional Neon cold-start 503s from `/ready` are accepted; a short probe would turn them into restart loops.

## Rollback

If the deployment misbehaves materially:

| Failure mode | Rollback path |
|---|---|
| Backend won't start / crashes on boot | Roll back the container image to the previous tag; investigate offline. |
| Database migration applied something destructive | Restore from PITR to a pre-migration timestamp ([disaster-recovery.md](disaster-recovery.md)); fix the migration in code; re-apply. |
| Frontend renders white screen after deploy | Roll back the Vercel deployment to the previous one (one click in Vercel UI). |
| Auth misconfigured (nobody can sign in) | If you have backend SSH/exec, fix env vars + restart. If not, redeploy with corrected env. |
| Encryption key rotated incorrectly (existing secrets unreadable) | Restore database from backup, restore prior encryption key, retry rotation more carefully. **The key is more important than any single secret encrypted with it** — never rotate without a tested restore plan. |

### Rolling back across the Prisma removal (SQLAlchemy migration phase 6)

Phase 6 changed no production data: production was already past `0001_baseline` (at `0004_langgraph_saver_tables`), so its first phase 6 deploy ran `alembic upgrade head` as a no-op, apart from any newer revision that was pending. `_prisma_migrations` and the two old checkpoint tables stay until phase 7. That makes rollback simple:

- **Rolling back to a pre-phase-6 image is a Cloud Run revision swap with no database action.** Shift traffic to the previous revision (`gcloud run services update-traffic <BACKEND_SERVICE> --to-revisions=<previous-rev>=100 --region=<REGION> --project=<PROJECT>`). The old image still contains the Prisma client but never imports it. If the checkpointer is also being rolled back, follow the [Rollback runbook](#rollback-runbook) above as well.
- **Re-running an old deploy workflow from a pre-phase-6 ref still works** while `_prisma_migrations` exists (until phase 7 drops it): its `prisma migrate deploy` finds nothing to apply. After phase 7, never re-run a pre-phase-6 deploy workflow: its `prisma migrate deploy` would fail on a non-empty schema without `_prisma_migrations`, loudly and without touching data.
- **Once a later revision reaches production** (for example `0005_execution_attempts` from the telemetry work), rolling back the image does not roll back the database. An image whose Alembic head is older than the database reports `/ready` 503 with `schema=mismatch`, but it keeps serving traffic, because the live startup probe is TCP on port 8080 and not `/ready`. An old deploy workflow run against that database fails closed at `alembic upgrade head` (the database's revision is not in the old checkout's history), so the deploy stops before `gcloud run deploy`. To redeploy an old build in that state, do it as a Cloud Run revision swap, not by re-running the old workflow.

### Rolling back across the execution-attempts migration (`0005_execution_attempts`)

Rolling the image back from a build whose head is `0005_execution_attempts` to one whose head is `0004_langgraph_saver_tables` is a Cloud Run revision swap; the database stays at 0005. Know three consequences:

- **`/ready` reports `schema=mismatch` (503) while traffic still flows.** The old image's Alembic head (0004) is older than the database's (0005), so the readiness check fails, but the live startup probe is TCP on port 8080, so the revision keeps serving. Do not treat the 503 as an outage signal in this state, and do not gate the swap on it.
- **Open attempts can be left behind.** The 0004 image does not know about `execution_attempts`. It cancels, lease-fails or otherwise ends executions without closing their attempt rows, so an execution is `completed`, `failed` or `canceled` while its attempt keeps `ended_at IS NULL` and the UI shows it as "In progress" forever. The 0005 image repairs this on its own: every `POST /internal/sweep` runs a reconciliation step that closes any open attempt whose execution is already terminal, in one guarded `UPDATE`. It sets `ended_at` to the execution's `completed_at` (or now), `result` from the execution's status, and `failure_code = 'closed_by_reconciliation'`. It is idempotent, never touches the attempt of a running or waiting execution, and runs in its own short session. After rolling forward again, wait for one sweep (or call the endpoint) and the history is consistent. While the 0004 image is live nothing runs this step, so attempts stay open until you roll forward.
- **The 0005 downgrade refuses while history exists.** `alembic downgrade 0004_langgraph_saver_tables` fails if `execution_attempts` has any rows or any `workflow_executions.pending_attempt_approval_id` is set, and the error states the counts. To proceed deliberately: export `execution_attempts` first (for example `\copy execution_attempts TO 'attempts.csv' CSV HEADER`), then `TRUNCATE execution_attempts;` and `UPDATE workflow_executions SET pending_attempt_approval_id = NULL;`, then run the downgrade. Prefer leaving the database at 0005 and swapping only the image.

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
