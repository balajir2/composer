# Postgres Setup

**Audience:** platform ops/SRE. Covers Neon-hosted Postgres. If you are using a different Postgres
provider, the Neon-specific steps (console UI, branch model, PITR) differ but the
`DATABASE_URL`/`prisma migrate deploy` steps are identical.

---

## 1. Provision a Neon project

1. Sign in at [neon.tech](https://neon.tech). Create a new project named `composer-prod`.
2. Neon creates a default branch named `main` and a default database named `neondb`. Use these for
   production. Rename as needed under **Project Settings** → **Branches**.
3. Create a separate branch named `dev` for local development (optional but recommended — dev and
   prod share the same schema but different data).

### Branch setup for multi-environment use

| Branch | Purpose |
|---|---|
| `main` | Production database |
| `dev` | Local development / integration tests |
| `ci` | Optional — ephemeral for CI runs (GitHub Actions postgres service is cheaper) |

---

## 2. Get the DATABASE_URL

In the Neon console → **Connection Details**, select the branch, then copy the **connection string**.

Format:

```
postgresql://<user>:<password>@<host>.neon.tech/<dbname>?sslmode=require
```

Example (not a real credential):

```
postgresql://composer_owner:abc123XYZ@ep-cool-meadow-12345.us-east-2.aws.neon.tech/neondb?sslmode=require
```

**Always include `?sslmode=require`.** Neon requires TLS and will reject plain TCP connections.

Set this value:
- As the `DATABASE_URL` secret for the `composer-backend` Cloud Run service (see [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md) — Secret Manager, not a plain env var). Composer's actual production deployment runs the backend on GCP Cloud Run, not Vercel; Vercel is documented separately ([vercel-setup.md](vercel-setup.md)) only as an alternate path for the frontend, and even then the FastAPI backend — the only thing that ever reads `DATABASE_URL` — still needs a long-lived host like Cloud Run, since it cannot run as a Vercel Serverless Function.
- In the local `.env` file for development.
- In CI secrets as `TEST_DATABASE_URL` if you run integration tests against Neon.

---

## 3. Run `prisma migrate deploy` (first-time bootstrap)

`prisma migrate deploy` applies all pending migrations from `prisma/migrations/` to the target
database. Run this once after provisioning (and after every schema migration that ships in a new
Composer release).

```bash
# From the Composer repo root, with DATABASE_URL pointing at the production Neon branch:
DATABASE_URL="postgresql://..." uv run prisma migrate deploy
```

Expected output:

```
The following migration(s) have been applied:
  migrations/20260420_initial_schema/migration.sql
  migrations/20260421_phase7_users/migration.sql
  migrations/20260422_phase9b_original_owner_email/migration.sql
  ...
All migrations applied successfully.
```

If a migration was already applied (e.g., you ran this twice), Prisma skips it. `migrate deploy` is
idempotent.

**Do not run `prisma migrate dev` against production.** `migrate dev` is for local development; it
interactively prompts and may reset the database. Always use `migrate deploy` in production.

---

## 4. Create the first admin user

There is no self-serve admin-promotion flow. The first admin must be created by ops via SQL.

### Step 1 — Register a Composer account

Have the ops engineer (or the user who will be admin) register via the API:

```bash
curl -X POST https://composer.example.com/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "ops@example.com", "password": "choose-a-strong-password"}'
```

This creates a `users` row with `role = 'member'`.

### Step 2 — Promote to admin via SQL

Connect to the Neon production branch via the Neon SQL editor (Console → **SQL Editor**) or via
`psql`:

```bash
psql "postgresql://...?sslmode=require"
```

Then run:

```sql
UPDATE users
SET role = 'admin'
WHERE email = 'ops@example.com';
```

Verify:

```sql
SELECT id, email, role, created_at FROM users WHERE email = 'ops@example.com';
```

Expected: `role = admin`.

The admin role grants:
- Read access to any workflow or execution, regardless of owner.
- Ability to PUT any workflow (flip `isPublic`, update nodes/edges).
- Access to `GET|PUT|DELETE /admin/llm-keys[/{provider}]`.
- Access to `PATCH /workflows/{id}/owner` and `PATCH /mcp-servers/{id}/owner`.

See [admin-operations.md](admin-operations.md) for a complete list of admin capabilities.

---

## 5. Neon point-in-time recovery (PITR)

Neon retains write-ahead log (WAL) history for point-in-time recovery. The retention window depends
on your Neon plan:

| Plan | PITR window |
|---|---|
| Free | 7 days |
| Pro | 7 days (configurable up to 30 days) |
| Enterprise | 30 days (configurable) |

### Creating a PITR restore

If you need to recover data from before a bad migration or accidental delete:

1. Open Neon console → **Branches** → **main** → **Restore**.
2. Select **Restore to point in time**.
3. Choose the timestamp (e.g., one hour before the bad migration).
4. Neon creates a new branch from that point. Do NOT restore directly to `main` — inspect the
   restored branch first.

```bash
# Connect to the restored branch and inspect data:
psql "postgresql://<restored-branch-connection-string>?sslmode=require"
SELECT count(*) FROM workflows;
SELECT count(*) FROM mcp_servers;
```

5. If the data looks correct, promote the restored branch to main (Neon console → **Promote to main**)
   or selectively copy rows.

### Manual backup

For a full logical backup at any time:

```bash
pg_dump "postgresql://...?sslmode=require" \
  --no-privileges \
  --no-owner \
  --format=custom \
  --file=composer-backup-$(date +%Y%m%d-%H%M%S).dump
```

Restore with:

```bash
pg_restore --dbname "postgresql://...?sslmode=require" composer-backup-YYYYMMDD-HHMMSS.dump
```

---

## 6. Connection pooling

Cloud Run's scale-to-zero behavior means a burst of traffic can spin up several backend instances
at once, each opening its own connection pool — a real spike even though the backend is a
long-lived process per instance, not a per-request serverless function. For production, use Neon's
built-in PgBouncer pooler:

In Neon console → **Connection Details** → toggle **Connection pooling**. The pooled connection
string has `-pooler` in the hostname:

```
postgresql://...@ep-cool-meadow-12345-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
```

Use this URL as the `DATABASE_URL` secret for the `composer-backend` Cloud Run service to avoid
exhausting Postgres's max connection limit as instances scale up.

Composer's Prisma client manages connections via `PRISMA_CLIENT_ENGINE_TYPE=library` (the default).
Pooling at the Neon PgBouncer layer is additive and safe.

---

## Cross-references

- [gcp-cloud-run-setup.md](gcp-cloud-run-setup.md) — where to set DATABASE_URL for the deployed backend
- [llm-keys.md](llm-keys.md) — LLM key storage in the same Postgres database
- [admin-operations.md](admin-operations.md) — admin SQL operations, migration, reconciliation
- [monitoring.md](monitoring.md) — Postgres query observability via Neon console
