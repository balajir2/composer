# LLM Keys Management

**Audience:** Bounteous ops/SRE. Covers the full lifecycle of LLM API keys in Composer: where they
live, how to set and rotate them, and how they flow from Postgres to Vercel.

---

## 1. How the system works

```
Ops machine
  composer keys set anthropic sk-ant-...
          │
          ▼
  Postgres llm_api_keys table
  (AES-256-GCM encrypted, key prefix stored for display)
          │
          ▼ composer keys sync --target vercel
  Vercel environment variables
  (e.g. ANTHROPIC_API_KEY=sk-ant-...)
          │
          ▼ Vercel redeploy
  Composer FastAPI app
  reads env vars at startup via get_settings()
```

**Key points:**

- Postgres is the single source of truth for all LLM keys.
- Vercel env vars are a derived, synced copy. They are never edited directly in the Vercel UI during
  normal operations.
- The running app does not query Postgres for keys at request time. It reads env vars loaded at
  startup. A Vercel redeploy is required to pick up new values.
- Keys are encrypted at rest using AES-256-GCM with `ENCRYPTION_KEY` (same mechanism as MCP OAuth
  tokens). Only the first 6 characters of the plaintext key (the `keyPrefix`) are stored
  unencrypted for display.
- The admin HTTP endpoints (`/admin/llm-keys`) and the CLI both decrypt in memory; the plaintext
  key is never stored outside the encrypted column.

---

## 2. Supported providers

Ten providers are recognized. The `composer keys` CLI and the `/admin/llm-keys/{provider}` endpoint
accept these exact lowercase strings:

| Provider string | Env var synced to Vercel |
|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `google` | `GOOGLE_API_KEY` |
| `groq` | `GROQ_API_KEY` |
| `langsmith` | `LANGCHAIN_API_KEY` |
| `tavily` | `TAVILY_API_KEY` |
| `firecrawl` | `FIRECRAWL_API_KEY` |
| `serper` | `SERPER_API_KEY` |
| `browserless` | `BROWSERLESS_API_KEY` |
| `gamma` | `GAMMA_API_KEY` |

Passing any other provider string returns HTTP 422 (API) or a CLI error.

---

## 3. `composer keys` CLI reference

The CLI is available after installing the Composer package (`uv sync --all-extras`). Run from the
repo root with `DATABASE_URL` set in the environment (or in `.env`).

### List all keys

```bash
composer keys list
```

Output:

```
provider       prefix    updated
-------------- --------- -------------------------
anthropic      sk-ant    2026-04-22T10:00:00+00:00
openai         sk-12a    2026-04-21T09:30:00+00:00
langsmith      lsv2_p    2026-04-20T14:00:00+00:00
```

Only the prefix is shown — never the full key.

### Set (or update) a key

```bash
composer keys set <provider> <value>
```

Example:

```bash
composer keys set anthropic sk-ant-api03-abc123...
```

To avoid the key appearing in shell history, pipe it from stdin:

```bash
cat /path/to/anthropic-key.txt | composer keys set anthropic -
```

This upserts the `llm_api_keys` row for the provider. It does not automatically sync to Vercel.

### Delete a key

```bash
composer keys delete <provider>
```

Example:

```bash
composer keys delete serper
```

Deletes the row from Postgres. The Vercel env var is not removed until you run `sync --prune`.

### Sync to Vercel

```bash
composer keys sync --target vercel
```

Requires `VERCEL_API_TOKEN` and `VERCEL_PROJECT_ID` in the environment:

```bash
VERCEL_API_TOKEN=... VERCEL_PROJECT_ID=prj_... composer keys sync --target vercel
```

Or set them in `.env` (dev machine only; never commit these to the repo).

The sync:
1. Reads all rows from `llm_api_keys`, decrypts each key.
2. Maps each provider to its env var name (see table in §2).
3. Upserts the env var in Vercel for `production` + `preview` environments.
4. Prints a diff summary:

```
Sync complete:
  added:     [gamma]
  updated:   [anthropic, openai]
  unchanged: [groq, langsmith, tavily, firecrawl, serper, browserless]
  pruned:    []
```

**After every sync, trigger a Vercel redeploy** so the running app picks up the new values:

```bash
npx vercel --prod --force
```

### Sync with prune

```bash
composer keys sync --target vercel --prune
```

`--prune` removes Vercel env vars for providers that exist in Vercel but have been deleted from
Postgres. Without `--prune`, deleted Postgres rows leave stale Vercel vars in place (harmless but
messy). `--prune` only removes the 10 recognized provider env vars; it does not touch unrelated
Vercel env vars.

---

## 4. Admin HTTP API reference

All endpoints require `role = admin`. Use `Authorization: Bearer <admin-jwt>`.

| Method | Path | Effect |
|---|---|---|
| `GET` | `/admin/llm-keys` | Returns `[{provider, keyPrefix, updatedAt}]` — never plaintext |
| `GET` | `/admin/llm-keys/{provider}` | Single key metadata; 404 if not set |
| `PUT` | `/admin/llm-keys/{provider}` | Body `{"value": "sk-..."}`. Upsert. 422 on unknown provider |
| `DELETE` | `/admin/llm-keys/{provider}` | 204 on success; 404 if not set |

To obtain an admin JWT, log in as an admin user: `POST /auth/login` → use `access_token`.

---

## 5. Key rotation runbook

Use this procedure when rotating an LLM provider key (e.g., Anthropic rotated your key, or you are
doing a scheduled rotation).

1. **Obtain the new key** from the provider's console.

2. **Set the new key in Postgres:**
   ```bash
   composer keys set anthropic sk-ant-new-key-value...
   ```

3. **Sync to Vercel:**
   ```bash
   VERCEL_API_TOKEN=... VERCEL_PROJECT_ID=prj_... composer keys sync --target vercel
   ```

4. **Trigger a Vercel redeploy:**
   ```bash
   npx vercel --prod --force
   ```

5. **Verify the running app uses the new key:**
   - Watch LangSmith or Vercel logs for a test execution.
   - Or hit `GET /admin/llm-keys/anthropic` to confirm the `keyPrefix` changed.

6. **Revoke the old key** in the provider's console once the new key is confirmed working.

Total downtime: zero. The old key continues to work until the redeploy completes; the new key takes
over atomically on the next Vercel function cold-start.

---

## 6. Emergency key revocation

If a key is leaked and must be revoked immediately:

1. **Revoke in the provider's console first.** This takes effect immediately regardless of Vercel.

2. **Delete from Postgres:**
   ```bash
   composer keys delete anthropic
   ```

3. **Sync with prune to remove from Vercel:**
   ```bash
   composer keys sync --target vercel --prune
   ```

4. **Redeploy:**
   ```bash
   npx vercel --prod --force
   ```

5. **Set the new replacement key** (from the provider) and re-sync once obtained.

After step 1, existing Vercel instances still have the revoked key in their environment, but calls
to the provider will fail (provider-side revocation is immediate). After the redeploy (step 4), the
revoked key is removed from Vercel entirely.

---

## 7. Troubleshooting

### Postgres has a key; Vercel does not

Run `composer keys sync --target vercel`. This is the normal outcome after adding a new key before
the first sync.

### Vercel has a key; Postgres does not

This can happen if a key was manually added to Vercel or if the Postgres row was deleted without
`--prune`. Options:
- Add the key back to Postgres: `composer keys set <provider> <value>`, then sync.
- Remove the stale Vercel var: `composer keys sync --target vercel --prune`.

### sync fails with 401 from Vercel

`VERCEL_API_TOKEN` is expired or has insufficient scope. Create a new token in Vercel →
**Account Settings** → **Tokens** (needs `project:write` scope) and update your `.env`.

### sync fails for some providers but succeeds for others

`composer keys sync` reports per-key failures and exits non-zero. Check the output — Vercel API rate
limits or transient errors cause partial failures. Re-running the sync is safe; it is idempotent.

### App starts up with empty provider key

`get_settings()` reads env vars at startup. If `ANTHROPIC_API_KEY` is empty in Vercel, the app
starts without error but requests that invoke Claude fail. After adding/syncing the key, redeploy
to inject the updated value.

---

## Cross-references

- [vercel-setup.md](vercel-setup.md) — where VERCEL_API_TOKEN/VERCEL_PROJECT_ID are configured
- [postgres-setup.md](postgres-setup.md) — Neon provisioning, ENCRYPTION_KEY setup
- [admin-operations.md](admin-operations.md) — admin promotion (required to use the HTTP API)
- [monitoring.md](monitoring.md) — LangSmith for verifying key-in-use
