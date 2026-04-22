# Admin Operations

**Audience:** Bounteous ops/SRE. Covers day-to-day and cutover operations performed by an admin
user or by ops via direct Postgres access.

---

## 1. Promoting a user to admin

Admin promotion is manual — there is no endpoint or UI. Ops runs SQL directly against the production
Postgres instance.

### Prerequisites

- Access to Neon console SQL editor, or `psql` with the production `DATABASE_URL`.
- The target user must have already registered at `POST /auth/register` (or via SSO in Phase 10).

### SQL snippet

```sql
-- Promote a user to admin
UPDATE users
SET role = 'admin'
WHERE email = 'ops@bounteous.com';

-- Verify
SELECT id, email, role, created_at
FROM users
WHERE email = 'ops@bounteous.com';
```

Expected result: `role = admin`.

### What admin grants

| Capability | Admin | Member |
|---|---|---|
| Read own workflows and executions | Yes | Yes |
| Read any user's private workflow | Yes | No (404) |
| Read any user's execution / stream | Yes | No (404) |
| PUT (update) any workflow | Yes | No (403) |
| DELETE any workflow | No — owner-only | No (403) |
| PUT any MCP server | Yes | No (403) |
| DELETE any MCP server | No — owner-only | No (403) |
| `GET /admin/llm-keys` | Yes | No (403) |
| `PUT /admin/llm-keys/{provider}` | Yes | No (403) |
| `PATCH /workflows/{id}/owner` | Yes | No (403) |
| `PATCH /mcp-servers/{id}/owner` | Yes | No (403) |

---

## 2. Running the OAB → Composer migration

The migration script reads a Convex JSON export produced by `npx convex export --prod` and inserts
the data into Composer's Postgres. Run this once at cutover.

### Produce the Convex export (on the OAB side)

```bash
# In the OAB repo, by the OAB ops team:
npx convex export --prod --path /tmp/oab-export
```

This produces a directory containing JSON files: `workflows.json`, `executions.json`,
`mcpServers.json`, `mcpOAuthTokens.json`, `users.json`, and others.

### Dry run (preview without writing)

```bash
OAB_MCP_OAUTH_ENCRYPTION_KEY=<oab-key> \
ENCRYPTION_KEY=<composer-key> \
DATABASE_URL=<composer-prod-db> \
composer migrate --export-dir=/tmp/oab-export --dry-run
```

Dry-run prints per-table counts, skipped tables (users, approvals, checkpoints — all per ADR-0022),
and any rows that would be skipped due to shape validation errors. Review before running without
`--dry-run`. No writes are performed.

### Execute migration

```bash
OAB_MCP_OAUTH_ENCRYPTION_KEY=<oab-key> \
ENCRYPTION_KEY=<composer-key> \
DATABASE_URL=<composer-prod-db> \
composer migrate --export-dir=/tmp/oab-export
```

Both encryption keys are required. The migration:
- Decrypts each MCP OAuth token with `OAB_MCP_OAUTH_ENCRYPTION_KEY`.
- Re-encrypts with `ENCRYPTION_KEY` (Composer's key).
- Writes `user_id = NULL`, `original_owner_email = <email from OAB users table>` on every migrated
  row.
- Skips rows that already exist (idempotent — safe to re-run).

Final output:

```
Migration complete.
  workflows inserted:        42  (3 skipped — shape validation errors)
  executions inserted:       187 (0 skipped)
  mcp_servers inserted:      15  (0 skipped)
  mcp_oauth_tokens inserted: 12  (0 skipped; 1 decryption failure — logged)
  in-flight executions:      4   (status set to 'failed'; not resumable)
```

### After migration

Migrated rows are invisible to non-admin users (because `user_id = NULL`). Run reconciliation (§3)
for each user who needs access to their data.

---

## 3. Reconciling a user post-migration

After a user registers on Composer with the same email they had in OAB, run reconciliation to claim
their migrated data.

```bash
DATABASE_URL=<composer-prod-db> \
composer reconcile --email alice@bounteous.com
```

This sets `user_id = <alice's cuid>` on every row in `workflows`, `workflow_executions`, and
`mcp_servers` where `original_owner_email = 'alice@bounteous.com'`.

Output:

```
Reconciling alice@bounteous.com ...
  Found user id: clm7xyzABC123
  Updated 12 workflows.
  Updated 48 executions.
  Updated 3 mcp_servers.
Done.
```

The command is idempotent — re-running it after the user already has their data sets the same
`user_id` again (no-op update).

If the user has not yet registered:

```
Error: no Composer user found with email alice@bounteous.com.
       Ask the user to register at /auth/register first.
```

### Bulk reconciliation at cutover

Collect all unique emails still needing reconciliation:

```sql
SELECT DISTINCT original_owner_email
FROM workflows
WHERE user_id IS NULL AND original_owner_email IS NOT NULL
ORDER BY original_owner_email;
```

Then run reconcile for each after users register:

```bash
while IFS= read -r email; do
  composer reconcile --email "$email"
done < /tmp/user-emails.txt
```

---

## 4. Reassigning a single workflow to a different user

Use `PATCH /workflows/{id}/owner` to reassign ownership of one workflow. Admin-only endpoint.

### By email

```bash
curl -X PATCH https://composer.bounteous.com/workflows/<workflow-id>/owner \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@bounteous.com"}'
```

The server looks up Alice's `user_id` by email and updates the workflow. Returns 200 with the
updated workflow object.

### By user_id

```bash
curl -X PATCH https://composer.bounteous.com/workflows/<workflow-id>/owner \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "clm7xyzABC123"}'
```

### Same for MCP servers

```bash
curl -X PATCH https://composer.bounteous.com/mcp-servers/<server-id>/owner \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@bounteous.com"}'
```

### Error cases

| Condition | Response |
|---|---|
| Caller is not admin | 403 |
| Workflow id does not exist | 404 |
| Email provided but no user with that email | 404 `"user with email 'x' not found"` |
| user_id provided but no user with that id | 404 `"user 'x' not found"` |

---

## 5. Getting an admin JWT for curl operations

```bash
curl -X POST https://composer.bounteous.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "ops@bounteous.com", "password": "..."}'
# → {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer"}
```

Use `access_token` as `Authorization: Bearer <token>`. Expires after `JWT_ACCESS_TTL_SECONDS` (1h).
Refresh via `POST /auth/refresh` using the refresh token.

---

## 6. Common user-facing issues

### "I can't see my workflow after migration"

**Cause:** The user's data was migrated with `user_id = NULL` (owner not yet reconciled).

**Fix:** Confirm the user has registered on Composer with the same email they used in OAB. Then run:

```bash
composer reconcile --email <their-email>
```

After reconciliation, their workflows appear immediately (no deploy needed).

### "I can see a workflow but can't edit it"

Check ownership:

```sql
SELECT w.id, w.title, w.user_id, u.email, w.is_public
FROM workflows w
LEFT JOIN users u ON w.user_id = u.id
WHERE w.id = '<workflow-id>';
```

- If `user_id` is NULL → reconcile the user (§3).
- If `user_id` belongs to someone else → use `PATCH /workflows/{id}/owner` (§4) if reassignment is
  warranted.

### "My MCP server shows as connected in OAB but not in Composer"

**Cause:** The MCP server was migrated but the OAuth token decryption failed (wrong OAB key or
corrupt token data). The `mcp_servers` row exists but the `mcp_oauth_tokens` row is missing.

**Diagnosis:**

```sql
SELECT ms.id, ms.name, ms.original_owner_email, ms.user_id,
       mot.id AS token_id
FROM mcp_servers ms
LEFT JOIN mcp_oauth_tokens mot ON mot.mcp_server_id = ms.id
WHERE ms.original_owner_email = 'alice@bounteous.com';
```

If `token_id` is NULL but the server should have tokens, the token was skipped during migration.

**Fix:** Have the user re-authorize the MCP server via the Composer UI OAuth flow. The existing
`mcp_servers` row is already in place; the OAuth flow writes a new `mcp_oauth_tokens` row.

### "User registered but reconcile says 'no user found'"

Email case mismatch. OAB stored `Alice@Bounteous.com`; Composer normalizes to lowercase.

```sql
SELECT email FROM users WHERE lower(email) = lower('Alice@Bounteous.com');
```

Run reconcile with the lowercase value: `composer reconcile --email alice@bounteous.com`

---

## Cross-references

- [postgres-setup.md](postgres-setup.md) — Neon access, psql setup
- [llm-keys.md](llm-keys.md) — LLM key management (admin endpoint coverage)
- [vercel-setup.md](vercel-setup.md) — obtaining the deployed API URL
- [monitoring.md](monitoring.md) — LangSmith traces for debugging execution issues
