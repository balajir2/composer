# Admin Guide

What admins do day-to-day through Composer's UI. For operational tasks that require Postgres access (promoting users, running migrations), see [`operations/admin-operations.md`](operations/admin-operations.md).

## What admin grants

| Capability | Admin | Member |
|---|---|---|
| Read own workflows / executions | ✓ | ✓ |
| Read any user's private workflow | ✓ | 404 |
| Read any user's execution / WebSocket stream | ✓ | 404 |
| Update any workflow (`PUT /workflows/{id}`) | ✓ | 403 |
| Reassign any workflow's owner (`PATCH /workflows/{id}/owner`) | ✓ | 403 |
| Update any MCP server's `isShared` flag | ✓ | 403 |
| LLM keys CRUD (`/admin/llm-keys`) | ✓ | 403 |
| LLM models CRUD + Verify (`/admin/llm-models`) | ✓ | 403 |
| User role management (`/admin/users`) | ✓ | 403 |
| Deployment settings (`/admin/deployment-settings`) | ✓ | 403 |
| All-workflows view (global feed at `/admin/workflows`) | ✓ | 403 |

Becoming admin is **not** self-serve — there's no UI to promote yourself. Run the SQL in [`operations/admin-operations.md`](operations/admin-operations.md#1-promoting-a-user-to-admin) (or have ops run it). Anyone with admin role then has full access via the UI.

## The admin console

Path: `/admin`. The sidebar exposes five sections.

### Users

Path: `/admin/users`.

Lists every registered user. Click a row to:

- Toggle role between `admin` and `member`. The change is immediate; the user sees their next page load reflect new permissions.
- Deactivate (soft-delete) a user. Sets `isActive=false`; the user can no longer sign in but their workflows + executions stay in place. To restore, flip the flag again.
- **Reset password**: issues the user a temporary password and sets `mustChangePassword=true`, so their next login forces them to pick a new one. Use this for a locked-out user who can't receive email, or as a break-glass option. Most users can now self-serve via **Forgot password?** on the login page — no admin action needed unless email delivery is down or the account has no verified inbox.

### LLM models

Path: `/admin/llm-models`.

Curates the model catalog designers see in the agent node's Model dropdown.

**Add a model**: click **Add model**, pick a provider, pick from the live model list (auto-fetched from the provider's `/models` API + filtered to actually-invocable models — see [decisions.md ADR-0023](decisions.md)) or enter manually.

**Verify a model**: every row has a **Verify** button. Issues a 1-token probe against the actual model with the configured API key. Outcomes:

| Status | Meaning | Side effect |
|---|---|---|
| ✓ verified | Model is invocable | Stamps `verificationStatus=ok`, `verifiedAt=now` |
| ✗ unavailable | Provider returned 404 / model-not-found | Stamps `verificationStatus=unavailable`, **auto-disables the row** so it disappears from designer dropdowns |
| key/auth error | API key rejected | No stamp change — fix the key, re-verify |
| network error | Couldn't reach provider | No stamp change — try again |

The auto-disable on `unavailable` is what closes the gap where (e.g.) Google retires `gemini-2.0-flash` for new keys: one click and it's out of the dropdown. Designers running workflows that referenced a now-disabled model get a fast-fail `ModelUnavailableError` at the agent executor instead of an opaque 404 buried in retry stacks.

**Disable / Delete** are also per-row actions. Disable hides the model from designers; Delete removes the row.

### LLM keys

Path: `/admin/llm-keys`.

Where the actual API keys live (encrypted, source of truth).

**Add or rotate a key**: open the row for the provider (Anthropic / OpenAI / Google / Groq / Tavily / Firecrawl / Serper / Browserless / LangSmith / Gamma) → paste the new value → save. The key is AES-256-GCM-encrypted; only the first 6 chars are shown afterwards.

**Test connection**: every row has a Test button that issues a minimal authenticated call to the provider (e.g. 1-token completion for LLMs, list-models for tools). Confirms the key works without you having to construct a curl by hand.

**Sync to runtime**: the FastAPI app reads keys from Postgres on startup, but if you want them in the runtime's env vars too (some hosts, some integrations), the `composer keys sync --target vercel` CLI pushes the current Postgres values to Vercel env vars. See [`operations/llm-keys.md`](operations/llm-keys.md).

**Not shown here — the `jira` and `confluence` nodes' per-node API tokens**: neither node type uses a centrally-managed key like the ones above. Their `domain`/`email`/`apiToken` fields live directly on the node and are entered per-workflow by the designer. The token is still AES-256-GCM-encrypted at rest and redacted (to a fixed `••••••••` marker) in every API response — it's just a separate storage path (inline in the workflow's `nodes` JSON) rather than the `LlmApiKey` table this page manages, so it has no row here and no "first 6 chars" prefix display.

### MCP servers

Path: `/admin/mcp-servers`.

Lists every MCP server registered in the system (across all users). The admin's main job here is the **`isShared` toggle** — when on, the MCP server is discoverable + usable by every designer. When off (default), only the user who registered it can use it.

The shared-token fallback (per ADR-0008): when User B uses a shared MCP server but doesn't have their own OAuth token, the server falls back to the original registrant's token. This is what makes "Bounteous-blessed Highspot MCP server" practical — admin registers + authorises once, every designer benefits.

### Deployment settings

Path: `/admin/deployment-settings`.

Per-deployment toggles stored as plain strings in `deployment_settings`. Currently used for:

- `tool.<name>.enabled` — admin can disable a built-in tool provider system-wide (e.g. block Browserless if your security team won't approve it)
- Future: feature flags, staged rollouts

Each row is a key + value text box; the meaning is documented inline. Adding a new setting is a backend change (see `src/api/admin_deployment_settings.py`).

### Workflows

Path: `/admin/workflows`.

Global feed of every workflow on the system. Useful for:

- Searching across all users (the regular `/designer` page only shows a user's own)
- Reassigning ownership when an employee leaves — click a row → Reassign → enter the new owner's email
- Setting `isPublic` / `isProduction` on someone else's workflow (e.g. publish a workflow they authored)
- **Managing access** — click a row → Manage access to grant or revoke a shared *assignment* on any workflow, same dialog a workflow owner sees from their own Settings page. This is where an admin gets visibility into (and control over) who a workflow has been shared with, separate from who owns it. Any member (not just admins) can look up people to share with via `GET /users/search` (rate-limited, 30/min/user) from their own workflow's "Manage access" panel — admins aren't required to broker every share.

## Common admin tasks

### Onboarding a new designer

1. They sign in (Azure SSO auto-creates the user; or they register via username/password if standalone).
2. **Default role is `member`** — no admin action needed unless they need elevated access.
3. They land on `/designer` and start cloning templates.

### Promoting a user to admin

1. Confirm they've registered (you can see them at `/admin/users`).
2. Run the SQL in [`operations/admin-operations.md`](operations/admin-operations.md#1-promoting-a-user-to-admin) — or via the `/admin/users/{id}/role` API if there's already an admin to issue the call.

### Rotating an LLM API key

1. **Admin → LLM keys** → open the provider's row → paste the new value → save.
2. Click **Test** to confirm it works.
3. If the key is also used by other services (LangSmith for tracing, etc.), follow [`operations/llm-keys.md`](operations/llm-keys.md) to sync to runtime env vars.

### Rolling out a new model

1. **Admin → LLM models** → **Add model** → pick provider, select from the live list.
2. Click **Verify** on the new row to confirm it's invocable with the current key.
3. Designers see it in the agent node's Model dropdown immediately (the dropdown re-fetches; no cache to bust).

### Removing a stale model

1. **Admin → LLM models** → **Verify** on the suspect row.
2. If it returns `unavailable`, the row auto-disables — no further action needed.
3. If you want to remove it entirely, click **Delete**. The row is gone, but workflows that referenced it will fail fast at the agent executor with a clear "model X is marked unavailable" error.

### Migrating data from OAB

One-shot at cutover. The procedure is in [`operations/admin-operations.md`](operations/admin-operations.md#2-running-the-oab--composer-migration). Requires both encryption keys (OAB's and Composer's) so MCP OAuth tokens can be decrypted from OAB and re-encrypted for Composer.

### Reassigning a workflow when an employee leaves

```bash
curl -X PATCH https://composer.bounteous.com/workflows/<workflow-id>/owner \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"email": "newowner@bounteous.com"}'
```

Or via the `/admin/workflows` UI — click the workflow → Reassign → enter the new owner's email.

The same pattern works for MCP servers via `PATCH /mcp-servers/{id}/owner`.

If the departing employee's teammate just needs continued access rather than a full ownership transfer — e.g. someone is out temporarily, or a workflow is genuinely co-owned by a small team — **grant them an assignment instead** (owner-settings panel → "Manage access", or `POST /workflows/{id}/assignments/{userId}`). Assignment gives full read+write access without changing who can delete the workflow or transfer it again later. Reassign the owner outright only when the departing employee's access should actually be revoked.

## Authz model in one paragraph

Three-layer auth: **NextAuth** mints a session token (Azure AD or Credentials), **Composer JWT** wraps that for backend session calls, **per-user API keys** (`ck_...`) authorise external invokes. Every API route uses `Depends(get_current_user_id)` or `Depends(ensure_admin)` to enforce per-call. Admin is one bit on the `users.role` column. Workflow visibility: public workflows are world-readable; private workflows return 404 (not 403) to non-owners so existence isn't leaked. Admins bypass the read/write check on workflows + MCP servers but **not** on delete (delete is owner-only — by design, to avoid an admin accidentally erasing someone else's work). The ADR with the full reasoning is [ADR-0025](decisions.md).
