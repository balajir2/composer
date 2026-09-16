# Phase 9 — Cutover readiness: Design

**Status.** Draft 2026-04-22.
**Related.** Phase 7a auth (ADR-0014/0015), Phase 7b workflow CRUD, Phase 5b SSE, Phase 8 security (ADR-0021), ADR-0022 (this phase — cutover policy).
**Active design doc:** [docs/design/2026-04-20-composer-python-port-design.md §Phase 9](../../design/2026-04-20-composer-python-port-design.md).

---

## 1. Goal

Take Composer from "feature-complete backend" to "production-shippable on Vercel + Postgres with internal OAB users migrated over."

Concretely:
- One-shot migration of existing OAB Convex data into Composer Postgres, preserving workflow/execution/MCP ownership intent via email-based reconciliation.
- Admin-role capabilities so ops can verify migrated data and manage ownership post-cutover.
- LLM API keys stored in Postgres (Composer-owned) with a deploy-time sync step to Vercel env vars.
- WebSocket streaming replacing SSE, aligned with IE's `DES-007` event contract.
- Deployment runbook + finalized `.env.example`.

## 2. Non-goals

- **No Azure SSO integration.** Azure SSO is Phase 10 (NextAuth-side). Phase 9 assumes users arrive via Composer's existing `/auth/register` or `/auth/login` paths; SSO integration reuses the same User-by-email identity.
- **No user migration.** OAB's `users` table is NOT copied. Clerk user IDs are dropped entirely. Email is the stable cross-system identity.
- **No Redis rate limiter.** Phase 8 in-memory limiter stays for Phase 9. Multi-worker distributed limiting deferred.
- **No SSRF protection on the `http` executor.** Deferred.
- **No `/auth/forgot-password` flow.** Users can't self-recover; ops resets via DB.
- **No RBAC beyond admin/member.** Admin is boolean-level privilege; no per-workflow ACLs, no groups.
- **No WebSocket bidirectional features.** Client→server messages (interactive `pause`, `get_full_state`, etc.) are not scoped. WebSocket is one-way server→client, same semantics as SSE was.
- **No LangGraph checkpoint migration.** JS checkpoint bytes are incompatible with Python. In-flight OAB executions complete on OAB or abandon.
- **No deployment automation beyond `composer keys sync`.** CI/CD pipelines are ops-side; docs explain what to wire up.

## 3. Sub-phase structure

Phase 9 is six sub-phases, **risk-first order:**

| # | Sub-phase | Ships |
|---|---|---|
| **9b** | Convex → Postgres migration | Migration script + `original_owner_email` column + reconciliation CLI |
| **9f** | Admin capabilities | Admin read bypass + publish bypass + ownership reassignment endpoints |
| **9e** | LLM keys in Postgres | `LlmApiKey` model + encrypted CRUD + `composer keys` CLI + Vercel sync |
| **9a** | WebSocket streaming | `/executions/{id}/ws` replacing SSE per DES-007 |
| **9c** | Deployment docs | `docs/deployment/` — Vercel, Postgres, LLM keys, admin ops, monitoring |
| **9d** | `.env.example` finalize | LLM-keys-dev-only marker + Vercel sync env vars + audit |

Rationale for order: 9b surfaces schema surprises first; 9f unblocks post-migration ops verification; 9e adds encrypted storage and is the biggest new code surface after 9b; 9a comes late because DES-007 event contract wants IE-source cross-check; 9c documents against the final shape; 9d finalizes env.example last.

---

## 4. Sub-phase 9b — Convex → Postgres migration

### 4.1 Source

**Input:** OAB ops runs `npx convex export --prod` on the OAB Convex deployment to produce a JSON snapshot directory. Migration script takes that directory as `--export-dir=<path>` input.

Rationale: one-shot cutover event (not continuous sync). Live-Convex credentials add ops risk for no gain. Export file is a stable, inspectable artifact.

### 4.2 Table mapping

| OAB table | Action |
|---|---|
| `workflows` | → Composer `workflows` |
| `executions` | → Composer `workflow_executions` (status only; see §4.4) |
| `mcpServers` | → Composer `mcp_servers` |
| `mcpOAuthTokens` | → Composer `mcp_oauth_tokens` (re-encrypt with Composer's `ENCRYPTION_KEY`) |
| `users` | **skip** — no user migration (see §2) |
| `approvals` | **skip** — audit-only data, low functional value after cutover (ADR-0022) |
| `checkpoints`, `checkpoint_writes` | **skip** — JS↔Python incompatible |
| `mcpOAuthStates` | **skip** — ephemeral, 10-min TTL |
| `arcadeAuth`, `rateLimits`, `cache` | **skip** — ephemeral/infra |
| `apiKeys`, `userLLMKeys`, `userToolKeys` | **skip** — Composer does not model per-user keys (LLM keys central; tool keys from env) |
| `userMCPs` | **skip** — OAB personal-MCP feature not in Composer |
| `uiBuilderConfigurations` | **skip** — UI layer (Phase 10 concern) |

### 4.3 User ownership — email-based reconciliation

OAB's `userId` on every migrated row is a Clerk ID. Composer does not migrate users. The bridge:

1. **Schema change (one Prisma migration):** add column `originalOwnerEmail String?` to `Workflow`, `WorkflowExecution`, and `McpServer`. No reference to "Clerk" in column name or comment — just "the email of the owner at migration time."
2. **Migration joins OAB `workflows.userId` → OAB `users.email`** via the in-memory lookup built from the OAB users export file.
3. **Migration writes `user_id = NULL` + `original_owner_email = <email>`** on every migrated row. NULL user_id means Phase 8 authz leaves the row invisible to non-owners/non-admins; `is_public=true` rows stay world-readable.
4. **Reconciliation CLI:** `composer reconcile --email alice@example.com` — looks up the Composer User with that email, UPDATEs all `workflow`/`workflow_execution`/`mcp_servers` rows where `original_owner_email = <that email>` to set `user_id = <user's cuid>`. Idempotent; safe to re-run.
5. **`original_owner_email` stays in the schema indefinitely** as an audit field. Drop in a future phase if desired.

**What Alice experiences at cutover:**
- Ops migrates OAB data via `composer migrate --export-dir=<path>`.
- Alice registers at Composer (`/auth/register` with `alice@example.com`) — gets a standard Composer User record.
- Ops (or a future post-login hook in Phase 10) runs `composer reconcile --email alice@example.com` — her workflows/executions/MCPs become visible to her.
- Alice logs in → sees her migrated workflows. Private workflows she owned stay private.

### 4.4 Execution state

Migrated executions keep `status`, `nodeResults`, `variables`, `input`, `output`, `error`, `startedAt`, `completedAt`. They do NOT keep `threadId` (generate fresh) because no LangGraph checkpoint to resume. Any OAB execution with `status="waiting_approval"` or `status="running"` is migrated as `status="failed"` with `error="migrated from OAB; in-flight state not recoverable"` — they cannot be resumed on Composer.

### 4.5 MCP OAuth tokens

OAB stores OAuth tokens encrypted with OAB's `MCP_OAUTH_ENCRYPTION_KEY`. Composer uses its own `ENCRYPTION_KEY` (AES-256-GCM). Migration requires **both keys at runtime**:

```bash
OAB_MCP_OAUTH_ENCRYPTION_KEY=... \
ENCRYPTION_KEY=... \
composer migrate --export-dir=<path>
```

For each `mcpOAuthTokens` row: decrypt with OAB key → re-encrypt with Composer key → write. If decryption fails (wrong key / corrupt data), log a warning and skip that row. Cross-MCP-user reconciliation later; `user_id = NULL` + `original_owner_email` on the `mcp_servers` row pins owner intent.

### 4.6 Error handling & idempotence

- **Dry-run mode:** `composer migrate --export-dir=<path> --dry-run` prints what would be inserted without writing.
- **Idempotence:** migrated rows carry a stable id derived from OAB's `_id` (deterministic transform — store the OAB id as `id` directly since both systems use opaque string IDs and Prisma's `@id` accepts any unique string, not only cuids). Re-running migration checks for existing row by id and skips.
- **Validation errors:** malformed workflow nodes/edges that violate Composer's shape validator → log + skip + report in summary output. Do not abort migration on one bad row.
- **Pre-flight check:** if OAB `userLLMKeys` has rows, log informational "N user-scoped LLM keys in source; skipping per ADR-0022." No flag required; the central-keys policy is fixed.

### 4.7 Tests

- Unit: migration transforms for each table with fixture JSON (no Postgres).
- Integration (real Neon): seed 2-user fixture OAB-export → run migration → verify row counts, `original_owner_email` populated, `user_id` NULL, `is_public` preserved.
- Integration: `composer reconcile` happy path — register user → reconcile → assert all rows now have `user_id = <cuid>`.
- Integration: dry-run prints, does not write.
- Integration: OAuth token re-encrypt round-trip (decrypt with source key → re-encrypt with target → decrypt again with target → matches).

---

## 5. Sub-phase 9f — Admin capabilities

Amends Phase 8 authz; no schema changes. `UserRole { admin, member }` already exists in Prisma.

### 5.1 Authz bypass for admins

| Endpoint | Phase 8 behavior | Phase 9f admin override |
|---|---|---|
| `GET /workflows/{id}` | 404 for non-owner private | Admin: 200 regardless |
| `GET /workflows` list | Filter to caller-visible | Admin: no filter, all rows returned |
| `GET /workflows/search` | Filter to caller-visible | Admin: no filter |
| `GET /executions/{id}` | 404 for non-owner | Admin: 200 regardless |
| `GET /executions` list | Scoped to caller | Admin: returns all |
| `GET /executions/{id}/events` | 404 for non-owner | Admin: 200 regardless |
| `GET /executions/{id}/ws` (new in 9a) | 9a default: close code 4403 for non-owner | Admin: accepted regardless of ownership |
| `PUT /workflows/{id}` | 403 for non-owner | Admin: 200 — can modify anything including `isPublic` |
| `DELETE /workflows/{id}` | 403 for non-owner | **No change — still strict owner-only** |
| `PUT /mcp-servers/{id}` | 403 for non-owner | Admin: 200 |
| `DELETE /mcp-servers/{id}` | 403 for non-owner | **No change — still strict owner-only** |

Rationale for leaving DELETE owner-only: destructive + irreversible. Needs an explicit admin-delete endpoint if future ops demand it; silent bypass is too dangerous.

### 5.2 Ownership reassignment

New endpoint `PATCH /workflows/{id}/owner`, admin-only.

Body: `{"user_id": "clm7..."}` OR `{"email": "alice@example.com"}`.

Behavior:
- If body carries `email`, server resolves it via `SELECT id FROM users WHERE email = $1`. If no match → 404 `"user with email {email!r} not found"`.
- If body carries `user_id`, server validates the user exists. If not → 404 `"user {user_id!r} not found"`.
- Update workflow `user_id` to the resolved target. Return 200 with the updated workflow.
- Non-admin caller → 403.
- Unknown workflow → 404.

Same endpoint on MCP servers: `PATCH /mcp-servers/{id}/owner` with identical semantics.

**Executions are NOT reassignable** — they're runtime artifacts, `user_id` = who initiated. No endpoint.

### 5.3 Dependency interaction with 9b reconciliation

`composer reconcile --email X` (from 9b) and `PATCH /workflows/{id}/owner` (from 9f) are two entry points to the same underlying service function (`assign_workflow_ownership`). The CLI is bulk-by-email; the API is single-by-id. Both require admin-grade authority (CLI is DB-direct; API checks `UserRole.admin`).

### 5.4 Admin promotion

Manual only: `UPDATE users SET role='admin' WHERE email = '<email>'` run by ops against Postgres directly. Documented in 9c's `admin-operations.md`. No endpoint.

### 5.5 Tests

- Unit: admin GET on other-user's private workflow → 200.
- Unit: member GET on other-user's private → 404 (Phase 8 regression).
- Unit: admin PUT on other-user's workflow → 200; can flip `isPublic`.
- Unit: admin DELETE on other-user's workflow → 403 (owner-only preserved).
- Unit: `PATCH /workflows/{id}/owner` with `email` resolves + reassigns.
- Unit: `PATCH /workflows/{id}/owner` with invalid email → 404.
- Unit: member (not admin) calling `PATCH .../owner` → 403.

---

## 6. Sub-phase 9e — LLM keys in Postgres

### 6.1 Prisma model

```prisma
model LlmApiKey {
  id             String   @id @default(cuid())
  provider       String   @unique
  encryptedKey   String   @map("encrypted_key")
  keyPrefix      String   @map("key_prefix")
  createdAt      DateTime @default(now()) @map("created_at")
  updatedAt      DateTime @updatedAt      @map("updated_at")

  @@map("llm_api_keys")
}
```

`provider` values: `anthropic`, `openai`, `google`, `groq`, `langsmith`, `tavily`, `firecrawl`, `serper`, `browserless`, `gamma`. No enum — string with app-level validation, so we can add providers without migrations.

`keyPrefix` is the first 6 chars of the plaintext key (e.g. `sk-ant`, `sk-12ab`) for UI display. Not a secret.

### 6.2 Encryption

Reuse existing `ENCRYPTION_KEY` (AES-256-GCM) from `src/security/encryption.py`. Same mechanism as MCP OAuth tokens. Decryption only happens at key-read time (in the CLI, the admin endpoints on read, and during `composer keys sync`).

### 6.3 Admin endpoints

All require `UserRole.admin` (Phase 9f authz).

- `GET /admin/llm-keys` → returns `[{provider, keyPrefix, updatedAt}]`. Never returns the plaintext.
- `GET /admin/llm-keys/{provider}` → returns `{provider, keyPrefix, updatedAt}`. Never returns the plaintext.
- `PUT /admin/llm-keys/{provider}` with body `{"value": "sk-..."}` → upsert. 200 on success.
- `DELETE /admin/llm-keys/{provider}` → 204 on success; 404 if not found.

### 6.4 CLI

```bash
composer keys list                          # list providers + prefixes + updated times
composer keys set <provider> <value>        # upsert one provider
composer keys delete <provider>             # delete one provider
composer keys sync --target vercel          # push current Postgres state to Vercel env vars
```

`composer keys set` can also read from stdin: `cat key.txt | composer keys set openai -` — keeps the key out of shell history.

### 6.5 Vercel sync

`composer keys sync --target vercel` steps:

1. Read all rows from `llm_api_keys` table.
2. For each row, decrypt with `ENCRYPTION_KEY`.
3. Map provider name to env var name (see §6.6).
4. Call Vercel API `POST https://api.vercel.com/v10/projects/{project_id}/env` (upsert — use `PATCH` if key already exists). Target `production` + `preview` environments.
5. For rows deleted from Postgres but still in Vercel: optional `--prune` flag removes them.
6. Print a diff summary: `{added: [...], updated: [...], unchanged: [...], pruned: [...]}`.

Requires `VERCEL_API_TOKEN` + `VERCEL_PROJECT_ID` env vars (documented in 9d's `.env.example` update).

### 6.6 Provider → env var mapping

| Provider | Env var |
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

### 6.7 Runtime behavior

Unchanged. `get_settings()` still reads env vars at app startup. Postgres is only consulted by the admin CRUD and by `composer keys sync` — never by running workflow code. Rationale: zero hot-path latency, no cache-coherency issues, existing code path preserved.

### 6.8 Tests

- Unit: encrypt/decrypt round-trip.
- Unit: admin PUT/GET/DELETE happy paths.
- Unit: member calling any admin endpoint → 403.
- Unit: unknown provider on PUT → 422 (validate against whitelist).
- Unit: GET never returns plaintext.
- Integration (real Neon): full set-list-delete cycle; sync to a dry-run Vercel mock.
- Unit: CLI commands via `click.testing.CliRunner`.

---

## 7. Sub-phase 9a — WebSocket streaming

### 7.1 Transport

Replace SSE with WebSocket. Remove `GET /executions/{id}/events` SSE endpoint. New endpoint: `GET /executions/{id}/ws` that upgrades to WebSocket.

**Path format.** `GET /executions/{id}/ws` — same URL shape as the SSE endpoint it replaces.

**Auth.** Via `Sec-WebSocket-Protocol` subprotocol header. Client: `new WebSocket(url, ["bearer", token])`. Server: reads the `bearer` subprotocol, validates the JWT from the second element, accepts connection or rejects 401. Token never appears in URL, access logs, or browser history.

**FastAPI support.** `starlette.websockets.WebSocket` + `websocket.accept(subprotocol="bearer")`.

### 7.2 Event contract (DES-007)

Mirror the shape documented in Composer's blueprint §7.3. Subject to IE-source verification checkpoint before 9a implementation begins.

```json
{ "type": "workflow_started",   "executionId": "...", "tenantId": null, "timestamp": "2026-04-22T..." }
{ "type": "node_started",       "executionId": "...", "tenantId": null, "nodeId": "...", "nodeName": "Agent",  "timestamp": "..." }
{ "type": "node_completed",     "executionId": "...", "tenantId": null, "nodeId": "...", "output": {...},      "timestamp": "..." }
{ "type": "node_failed",        "executionId": "...", "tenantId": null, "nodeId": "...", "error": "...",       "timestamp": "..." }
{ "type": "workflow_completed", "executionId": "...", "tenantId": null, "output": {...},                       "timestamp": "..." }
{ "type": "approval_required",  "executionId": "...", "tenantId": null, "approvalId": "...", "message": "...", "timestamp": "..." }
```

Fields:
- `tenantId`: always `null` in standalone Composer; reserved for IE embedded mode (Phase 10).
- `timestamp`: ISO 8601 UTC.
- `executionId`: `workflow_executions.id`.
- `nodeId`: `workflows.nodes[].id`.
- `nodeName`: `workflows.nodes[].data.label` if present, else `nodes[].type`.

Dropped from SSE: `stream-chunk` (LLM token streaming). DES-007 doesn't cover it; Phase 10 frontend currently has no live-token rendering. Can be re-added as a Composer-specific extension `type: "agent_stream_chunk"` later if frontend needs it.

### 7.3 Connection lifecycle

1. Client connects to `wss://.../executions/{id}/ws` with subprotocol `["bearer", token]`.
2. Server validates JWT. If invalid → close with code 4401 (app-defined code in the WebSocket 4000–4999 range, chosen to mirror HTTP 401). If user is not owner AND not admin → close with code 4403 (mirrors HTTP 403).
3. Server accepts (`accept(subprotocol="bearer")`).
4. Server sends a **snapshot event** — one `workflow_started` or `workflow_completed` (or `approval_required`, whichever fits current state) reflecting the stored `workflow_executions.status` and any pending approval.
5. Server subscribes to the in-process `ExecutionEventBus` for this execution, forwards events as they arrive.
6. Keepalive: server sends a WebSocket ping frame every 15s (Starlette handles this natively).
7. When the execution reaches a terminal state (`completed`, `failed`), server sends the terminal event and closes (code 1000 normal).
8. Client disconnect → server cleans up subscription.

**No reconnection/resume protocol** — if the client drops mid-execution and reconnects, the fresh snapshot reflects the latest stored state but replayed events are lost. Matches Phase 5b SSE behavior; future enhancement can add event ID + cursor-based resume.

### 7.4 Admin override

Admin users can subscribe to any execution's WebSocket (consistent with 9f read bypass on events). Non-admin, non-owner → close code 4403 on the handshake.

### 7.5 Event bus adaptation

`src/engine/events.py` currently emits Phase 5b SSE event shapes (`node-start`, `status-change`, etc.). Two options:

- **A. Emit both old + new event shapes internally** — transitional. Requires `events.py` to know both vocabularies. Ugly.
- **B. Switch event bus to DES-007 shape; SSE removal is atomic with 9a.** Cleaner but bundles the SSE removal with the WS addition.

Going with **B**. `events.py` publishes DES-007-shaped events; `/executions/{id}/events` SSE endpoint is deleted in the same commit. Phase 8 regression tests for SSE (`tests/unit/api/test_events_stream.py`) are ported to WebSocket tests (`tests/unit/api/test_events_ws.py`).

### 7.6 IE-source verification checkpoint

Before 9a implementation kicks off, verify DES-007 event contract against actual IE source. If the IE team's spec differs from Composer's blueprint §7.3 (extra fields, renamed types, different snake/camel, etc.), file a note in this spec and adjust the implementation plan before coding. This is a pre-plan gate, not an implementation-time gate.

### 7.7 Tests

- Unit: WebSocket handshake with valid JWT → accepted.
- Unit: WebSocket handshake with missing subprotocol → 400.
- Unit: WebSocket handshake with invalid JWT → close 4401.
- Unit: non-owner non-admin → close 4403.
- Unit: admin of non-owner-execution → accepted.
- Unit: snapshot event on connect matches stored status.
- Unit: events broadcast through event bus reach subscribed client in correct shape.
- Unit: terminal event + close on workflow_completed.
- Integration (real Neon): two users — A's WebSocket works; B's attempt on A's execution closes 4403; admin's on A's works.

---

## 8. Sub-phase 9c — Deployment docs

Create `docs/deployment/` directory with five markdown files. No code changes.

### 8.1 `vercel-setup.md`
- Prerequisite: Vercel account, project created
- App deploy flow (connect GitHub repo → deploy main)
- Domain setup
- Required env vars (refer to `.env.example` + 9d updates)
- How to rotate `VERCEL_API_TOKEN`

### 8.2 `postgres-setup.md`
- Neon setup (create branch for prod)
- Initial `prisma migrate deploy`
- Creating the admin user (first-time bootstrap)
- Backups (Neon's point-in-time recovery)
- How to read the DATABASE_URL format

### 8.3 `llm-keys.md`
- How the Postgres-SoT + Vercel-sync flow works (diagram-ish)
- `composer keys` CLI reference
- Rotation runbook: update key in Postgres → `composer keys sync` → redeploy (Vercel picks up new env on next deploy)
- Emergency key revocation: delete from Postgres, sync with `--prune`, redeploy
- What happens if Postgres has a key Vercel doesn't: sync handles it
- What happens if Vercel has a key Postgres doesn't: manual delete in Vercel UI or add `--prune` to sync

### 8.4 `admin-operations.md`
- Promoting an admin: SQL snippet
- Running the OAB→Composer migration: `composer migrate --export-dir=<path>`
- Reconciling a user post-SSO: `composer reconcile --email X`
- Reassigning a single workflow: `PATCH /workflows/{id}/owner`
- Common troubleshooting (user reports "I can't see my workflow" after migration)

### 8.5 `monitoring.md`
- LangSmith setup (enable `LANGCHAIN_TRACING_V2=true`; assign project)
- Structured logging (Python logging → stdout → Vercel log drain)
- Error rate monitoring (Vercel observability or external APM)
- Rate-limit observability (in-memory; resets on restart)
- What to watch after cutover

---

## 9. Sub-phase 9d — `.env.example` finalize

Three changes to [.env.example](../../.env.example):

### 9.1 LLM keys → mark dev-only

Update the LLM keys comment block:

```
# ─── LLM providers ────────────────────────────
# DEV-ONLY override. In production, these values are managed in Postgres
# (Composer's `llm_api_keys` table) and synced to Vercel env vars via
# `composer keys sync --target vercel`.  Setting them here overrides
# the synced values for local dev only.
# ...
```

Same treatment for the `LANGCHAIN_*`, `TAVILY_API_KEY`, `SERPER_API_KEY`, `FIRECRAWL_API_KEY`, `BROWSERLESS_API_KEY`, `GAMMA_API_KEY` blocks.

### 9.2 Vercel sync env vars (new block)

```
# ─── Deploy sync (Phase 9e) ───────────────────
# For `composer keys sync --target vercel`.  Dev machines don't need these.
# VERCEL_API_TOKEN: Vercel account token with env-var-write scope.
# VERCEL_PROJECT_ID: ID of the Vercel project to push env vars into.
VERCEL_API_TOKEN=
VERCEL_PROJECT_ID=

# ─── Migration (Phase 9b) ─────────────────────
# For `composer migrate` — set when running the OAB→Composer migration.
# OAB_MCP_OAUTH_ENCRYPTION_KEY: the key OAB used to encrypt mcpOAuthTokens.
# (Composer's own ENCRYPTION_KEY is already set above.)
OAB_MCP_OAUTH_ENCRYPTION_KEY=
```

### 9.3 Audit for missing keys

Cross-check Phase 8's rate-limit settings — those are `Settings` fields, not env vars, so no `.env.example` changes. Phase 7's User table doesn't need new env. Only Phase 9e + 9b introduce new env vars.

Confirm all env-var-read settings in `src/config.py` are represented in `.env.example`.

---

## 10. Decisions (for ADR-0022)

Backfill into `docs/design/decisions.md` as ADR-0022:

1. **No user migration.** OAB users are not copied to Composer. Clerk IDs are dropped. Email is the stable cross-system identity.
2. **Email-based reconciliation.** `original_owner_email` column on migrated rows; `user_id = NULL` at migration time; `composer reconcile --email X` CLI maps rows to post-register/SSO users.
3. **Admin bypass scope.** Admins bypass read/publish authz on workflows/executions/MCPs. Admin does NOT bypass DELETE; admin does not see executions as reassignable.
4. **LLM keys in Postgres.** Composer owns the source of truth. Runtime still reads env vars; deploy-time sync script pushes to Vercel. Same encryption mechanism as MCP OAuth tokens.
5. **WebSocket replaces SSE atomically.** Event bus switches to DES-007 shapes in the same commit; SSE endpoint removed; streaming tests migrated.
6. **DES-007 event contract from blueprint.** Shape taken from Composer's own blueprint doc §7.3 with a pre-plan IE-source verification gate.
7. **Skip approvals + checkpoints migration.** Approvals are audit-only; LangGraph JS checkpoints are Python-incompatible. Both are dropped; in-flight OAB executions are not recoverable on Composer.

---

## 11. Error model

- Migration (9b): per-row errors logged + skipped; summary printed at end. Aborting is one specific non-recoverable case (Postgres connection loss).
- Admin API (9f, 9e): standard HTTPException with tight 404s (preserve info-leak policy from Phase 8) and 403 on member-calling-admin.
- WebSocket (9a): close codes — 1000 normal, 4401 auth, 4403 forbidden, 1011 internal error.
- `composer keys sync` (9e): partial failure on Vercel API error → report which keys failed, exit non-zero.

---

## 12. Phase-exit criteria

All six sub-phases shipped. Specifically:
- [ ] `composer migrate` runs green against real Neon with a fixture OAB export.
- [ ] `composer reconcile --email X` reassigns rows end-to-end.
- [ ] Admin can read/publish/reassign any workflow or MCP server.
- [ ] `composer keys` CLI set/list/delete/sync all work; sync against Vercel (real API call in staging if feasible, otherwise mock + manual smoke).
- [ ] WebSocket `/executions/{id}/ws` replaces SSE; event bus emits DES-007 shapes; tests ported.
- [ ] `docs/deployment/` has all five files.
- [ ] `.env.example` reflects new deploy-sync and migration env vars.
- [ ] CHANGELOG + ADR-0022 backfilled with commit range.
- [ ] ~X unit tests green (exact count determined at Task-8 phase-exit); 2–4 new integration tests green against real Neon.

---

## 13. Risks + open questions

| Risk / Q | Mitigation |
|---|---|
| DES-007 event contract drift vs. IE actual source | Pre-plan verification gate in 9a; if drift found, log + adjust the implementation plan before coding |
| OAB export format (Convex JSON shape) differs from expected | Validate early in 9b via a fixture; parsers should be schema-tolerant |
| Existing Phase 8 tests break when event shapes change (9a) | Migrate SSE event tests to WS tests in the same 9a commit |
| Vercel API changes / rate-limits / auth scope | `composer keys sync` retries with backoff; partial failures reported row-by-row |
| Admin bypass introduces subtle authz regressions on existing endpoints | Every admin bypass has a paired negative test (member on same endpoint → 404/403) |
| User registers with different casing of email than OAB had | Normalize email to lowercase on both sides — at User.create and at `original_owner_email` write |
| Multi-user email collision (two OAB users, same email, different clerkIds) | Log + abort migration with explicit error; this must be fixed in the OAB export by ops before re-running |
