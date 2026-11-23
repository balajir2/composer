# API Reference

Composer's HTTP API surface, grouped by audience. Auto-generated OpenAPI is at `/docs` (Swagger UI) and `/openapi.json` — that's authoritative. This doc is the reading-friendly tour.

## Authentication

Three modes, used by different callers:

| Mode | How | Used by |
|---|---|---|
| **Composer JWT (HS256)** | `Authorization: Bearer <jwt>` | Frontend session calls (after NextAuth mints + the backend wraps via `/auth/sso-exchange` for SSO or `/auth/login` for standalone) |
| **Per-user API key** | `Authorization: Bearer ck_<key>` | External invokes (`POST /api/run/{slug}`) |
| **Dev fallback** | No header | Only when `ENVIRONMENT != production` — server returns user_id `dev` with role `member` |

Admin-only routes additionally require `User.role = 'admin'` on the caller.

## Rate limits

In-memory token bucket per route key + client. Defaults:

| Route | Limit | Bucket key |
|---|---|---|
| `POST /auth/login` | configured (`rate_limit_login_per_minute`) | client IP |
| `POST /auth/register` | configured (`rate_limit_register_per_minute`) | client IP |
| `POST /auth/refresh` | configured (`rate_limit_refresh_per_minute`) | client IP |
| `POST /auth/forgot-password` | 5 / min (`rate_limit_forgot_password_per_minute`) | client IP |
| `GET /users/search` | 30 / min (`rate_limit_users_search_per_minute`) | user_id |
| `POST /api/run/{slug}` | configured (`rate_limit_api_run_per_minute`) | API key |
| `POST /uploads/extract-text` | 20 / min | user_id |
| `GET /approvals/email/{token}` | 20 / min (`rate_limit_approval_email_per_minute`) | client IP |
| `POST /cloud-storage/connections/{id}/picker-token` | 10 / min (`rate_limit_picker_token_per_minute`) | user_id |

429s carry a `Retry-After` header.

## Workflows

```
GET    /workflows?mine={true|false}&isTemplate={true|false}&isPublic={true|false}&category=<x>&limit=N&offset=N
GET    /workflows/search?q=<query>&limit=N
GET    /workflows/{id}
POST   /workflows                                       (create)
PUT    /workflows/{id}                                  (update — owner, admin, or an assignee)
DELETE /workflows/{id}                                  (owner only — admins cannot)
PATCH  /workflows/{id}/owner                            (admin — reassign)
PATCH  /workflows/{id}/admin-flags                      (admin — set isPublic / isProduction / externalSlug)
GET    /workflows/{id}/assignments                      list assignees (owner or admin)
POST   /workflows/{id}/assignments/{userId}              grant full read+write access (owner or admin)
DELETE /workflows/{id}/assignments/{userId}              revoke access (owner or admin)
```

`mine=true` always means "scope to the calling user's own workflows" regardless of role — admins asking for `mine=true` don't get the global feed (only `mine=false` or unset triggers admin-sees-all). It also includes workflows the caller is *assigned* to but doesn't own, not just workflows they own.

The `nodes` and `edges` arrays are validated against the discriminated union of node types defined in `src/engine/workflow.py`. Validation errors come back as `422` with FastAPI's standard `{detail: [{loc, msg, type}, ...]}` shape.

**Assignment** grants full read+write access (open, edit, run) to the target workflow — there's no view-vs-edit split. It's independent of the single `userId` owner field, which continues to govern `DELETE` and `PATCH .../owner`. Only the owner or an admin can grant/revoke.

## Executions

```
POST /executions                                        body: {workflowId, input, idempotencyKey?: string}
GET  /executions?workflowId=<id>&status=<x>&limit=N&offset=N
GET  /executions/{id}
POST /executions/{id}/resume                            body: {decision: "approved"|"rejected", note?: string}
POST /executions/{id}/cancel
DELETE /executions/{id}
POST /executions/delete-bulk                            body: {executionIds?: string[], allInScope?: boolean}
WS   /executions/{id}/ws                                stream node events
```

`POST /executions` is open to the workflow's owner, an admin, an assignee, and anyone for a public workflow; everyone else gets the same 404 as a missing workflow. It returns the new `executionId` immediately; the run continues in the background. Subscribe to the WebSocket for live updates or poll `GET /executions/{id}` for the final state.

`idempotencyKey` (optional): scoped per workflow and per caller — a repeated call with the same key from the same caller returns the original execution instead of starting a duplicate one. Another user sending the same key on the same workflow is unaffected: they start (and can replay) their own execution, and can't see or collide with anyone else's. A DB-level unique constraint on `(workflow_id, user_id, idempotency_key)` closes concurrent-create races. Maintenance marks expired attempts failed and never automatically replays them; a caller retry with the same key still returns that original execution. Omit it and every call creates a new execution.

`POST /executions/{id}/cancel` — owner or admin; accepts `queued`, `running`, or `waiting_approval`, and returns 409 for a terminal execution or a lost conditional transition. Cancellation is terminal and cooperative: the executor checks stored status before and after each node. A running node, including its internal agent model/tool loop, may finish before cancellation is observed. Already-started external side effects (Jira, email, HTTP) may finish. Completion, failure, or approval-pause writes that lose to cancellation cannot overwrite `canceled` or emit their outcome events/approval emails. Canceling after a committed approval pause remains valid. Cancellation retains the execution, LangGraph checkpoints, pending writes, variables, node results, and event history until the applicable retention policy removes them. Ordinary `POST /executions/{id}/resume` only accepts `waiting_approval` and rejects canceled executions with 409; retained data does not make cancellation resumable.

`DELETE /executions/{id}` and `POST /executions/delete-bulk` reject (409) or skip active (`queued`/`running`/`waiting_approval`) executions. Cancel or wait for a terminal state before explicitly deleting durable data.

Statuses: `queued` / `running` / `waiting_approval` / `completed` / `failed` / `canceled`.

Each active processing attempt defaults to **29 minutes** (`EXECUTION_MAX_RUNTIME_SECONDS=1740`). The shutdown grace defaults to 30 seconds; the Cloud Tasks dispatch deadline and worker lease both derive from runtime plus grace (1770 seconds by default). An approval resume starts a fresh attempt. These processing limits are independent of the durable thread's lifetime: a workflow may wait months for approval without holding a worker, subject to retention policy and any operator-configured approval expiration.

Failed executions expose `failureCode` alongside `error`:

| Code | Meaning |
|---|---|
| `execution_timeout` | The active attempt exceeded its processing budget. |
| `lease_expired` | A claimed worker's lease expired before the attempt finished. |
| `orphaned_running` | A running attempt has no lease and exceeded its claim grace. |
| `execution_error` | An executor or worker error ended the attempt. |

Expired leases become terminal failures. Maintenance never automatically replays the workflow or its external side effects.

The WebSocket emits these event types:

```json
{"type": "node_started",     "executionId": "...", "payload": {"nodeId": "agent-1", "nodeName": "Answer Question", ...}}
{"type": "node_completed",   "executionId": "...", "payload": {"nodeId": "agent-1", "input": ..., "output": ..., "durationMs": 1234}}
{"type": "node_failed",      "executionId": "...", "payload": {"nodeId": "agent-1", "error": "...", "durationMs": 1234}}
{"type": "execution_completed", "executionId": "...", "payload": {"status": "completed", "output": ...}}
```

`durationMs` (P1-5) is computed once, universally, in `wrap_executor_with_events` — the single wrapper every node type flows through — rather than per-executor. The same node also gets `startedAt`/`completedAt`/`durationMs` merged into its `nodeResults[nodeId]` entry in the persisted `WorkflowExecution.nodeResults` column, so timing survives a WebSocket disconnect and is visible on `GET /executions/{id}` after the fact, not just in the live stream.

## Approvals

```
GET /approvals/email/{token}
```

Public, unauthenticated — no `Authorization` header, no Composer account. The signed token in the path *is* the credential. Set when a `user-approval` node has `approverEmail` (and optionally `approverCc`) configured: the moment the workflow pauses at that node, Composer emails the approver two signed one-click links (Approve / Reject).

Behavior: verifies the token, atomically transitions the execution out of `waiting_approval` (a stale or already-used token is a no-op), resumes the workflow in the background, then redirects (`303`) to `{FRONTEND_URL}/approval-result?status=approved|rejected|invalid`. In-app approval via `POST /executions/{id}/resume` continues to work regardless, as a fallback.

Rate-limited (`rate_limit_approval_email_per_minute`, default 20/min/IP) — headroom for a legitimate double-click plus email-security-scanner link prefetching.

The link lifetime and optional approval expiration are independent:

- **`approval_link_ttl_hours`** (default 72h) — how long the emailed link itself stays valid. After it expires, in-app approval still works.
- **`approval_wait_timeout_hours`** (default **0**, disabled) — no automatic approval expiration by default. Operators may opt into a positive deployment-wide timeout, which auto-fails `waiting_approval` executions after that many hours. Waiting holds durable Postgres/checkpoint data and no live compute; it consumes none of an active attempt's processing budget.

## External invoke

```
POST /api/run/{slug}                body: {input, sync?: boolean, timeoutSeconds?: number (max 300), idempotencyKey?: string}
```

`idempotencyKey` behaves exactly as in `POST /executions` above (same replay/race semantics), scoped per resolved workflow and per caller.

Auth: `Authorization: Bearer ck_<api_key>`.

Authz:
- The workflow must have `isProduction=true` and the slug match.
- Public workflows are callable by any valid API key.
- Private workflows are callable only by the owner's API keys (or an admin's).
- All other cases return 404 (not 403, to avoid leaking existence).

**Async (default)** response:
```json
{
  "executionId": "cm...",
  "workflowId": "cm...",
  "status": "running",
  "streamUrl": "ws://composer.example.com/executions/cm.../ws"
}
```

**Sync** (`"sync": true`) response — waits up to `timeoutSeconds` (default 60, max 300) and returns the final state:
```json
{
  "executionId": "cm...",
  "workflowId": "cm...",
  "status": "completed",
  "output": "..."
}
```

Sync mode falls back to the async shape if the run hasn't reached a terminal state by `timeoutSeconds`.

**Input cap**: `max_execution_input_bytes` (default 1 MB). 413s carry the limit.

## API keys (per-user)

```
POST   /api-keys                            body: {label, expiresAt?}      (returns plaintext ONCE)
GET    /api-keys                            list mine
DELETE /api-keys/{id}                       revoke
```

Plaintext keys are shown exactly once on creation. The hash + first-6-char prefix are stored. Lost a key → revoke it and create a new one.

## Uploads

```
POST /uploads/extract-text                  multipart/form-data — field `file`
```

Returns:
```json
{
  "filename": "policy.pdf",
  "content_type": "application/pdf",
  "size_bytes": 187432,
  "text": "..."
}
```

Supported: `.txt`, `.md`, `.markdown`, `.rst`, `.log`, `.pdf`, `.docx`. 10 MB cap. No persistent storage — extraction happens in-memory and bytes are released on return.

## Auth

```
POST /auth/register                       body: {email, password, displayName?}
POST /auth/login                          body: {email, password}
POST /auth/refresh                        body: {refreshToken}
POST /auth/disconnect                     client-side token drop
POST /auth/sso-exchange                   body: {azureToken}     ← SSO entry point
POST /auth/change-password                body: {currentPassword?, newPassword}   (session or password_change token)
POST /auth/forgot-password                body: {email}                          (always 204 — no account-enumeration leak)
POST /auth/reset-password                 body: {token, newPassword}             (anonymous — token proves identity)
```

`/auth/register` and `/auth/login` are only registered when `COMPOSER_DEPLOYMENT_MODE=standalone`. Embedded mode expects the parent IE app to mint JWTs that Composer validates.

`/auth/forgot-password` and `/auth/reset-password` are the self-service counterpart to the admin-forced reset (`mustChangePassword` + `/auth/change-password`) — both share the same `password_change` JWT (30-minute TTL). `/auth/forgot-password` is rate-limited (`rate_limit_forgot_password_per_minute`, default 5/min/IP) and always returns `204`, whether or not the email matches an account, so the response never leaks account existence. `/auth/reset-password` accepts only a `password_change` token — access and refresh tokens are rejected with `400`.

## Users

```
GET /users/search?q=<query>&limit=N       body: none — any authenticated active user
```

Returns `id`/`email`/`displayName` only, rate-limited (`rate_limit_users_search_per_minute`, default 30/min/user). Lets non-admin workflow owners find people to share a workflow with — the full `/admin/users` listing stays admin-only.

## MCP servers

```
GET    /mcp-servers                       list (own + shared)
POST   /mcp-servers                       register
DELETE /mcp-servers/{id}                  owner-only
PATCH  /mcp-servers/{id}/shared           admin — flip isShared
PATCH  /mcp-servers/{id}/owner            admin — reassign
PATCH  /mcp-servers/{id}/oauth-config      owner — replace encrypted OAuth client configuration
POST   /mcp-servers/{id}/test-connection  health check
POST   /mcp-servers/{id}/oauth/authorize  generate the OAuth authorization URL
POST   /mcp-servers/{id}/oauth/disconnect remove the caller's stored OAuth token
GET    /oauth/callback                    OAuth redirect URI (handled internally)
```

Six critical MCP fixes from OAB's April 2026 debugging session are encoded — RFC 8707 `resource` parameter on every OAuth grant, manual `tools/list` (skipping Anthropic's native MCP connector that 73K-char tool definitions broke), `inputSchema` (camelCase) tolerance, server-side token retrieval (tokens never transit the client), shared-server token fallback, explicit LangSmith config threading. See [`decisions.md`](decisions.md) ADR-0007–ADR-0010 for the full story.

## Cloud storage (Google Drive)

```
GET  /cloud-storage/google-drive/authorize          kick off OAuth flow, returns authorizeUrl
GET  /cloud-storage/google-drive/callback           OAuth redirect URI (handled internally)
GET  /cloud-storage/connections?provider=<x>        list mine
POST /cloud-storage/connections/{id}/picker-token   one-time access token for the Google Picker embed
```

Backs the `file-trigger`/`file-write`/`download-pdf` nodes' Google Drive support. `authorize`/`callback` follow the same Authorization Code pattern as MCP OAuth (`src/integrations/google_drive/oauth.py`, structurally mirroring `src/mcp/oauth.py`), but against a single fixed-provider OAuth app (client id/secret from settings) rather than a per-record `oauthConfig`. Tokens are encrypted at rest (`CloudStorageConnection`) and never returned to the client — `picker-token` hands back a short-lived access token solely for the one call the Google Picker widget itself requires (`setOAuthToken`), refreshing it server-side via `get_valid_drive_access_token` first if needed. Returns 404 for a connection owned by another user (not 403, matching the platform's private-resource convention) and 409 if the stored refresh token has expired, so the frontend can prompt to reconnect.

The actual folder polling is server-side and unauthenticated-by-caller: `POST /internal/poll-file-triggers`, OIDC-authenticated like `/internal/claim-and-run`/`/internal/sweep` (ADR-0033), triggered on a fixed 5-minute cadence by Cloud Scheduler — not part of this API surface, since nothing outside Composer's own infrastructure calls it. See [`architecture.md`](architecture.md) for the poll flow.

## LLM models

```
GET   /llm-models?provider=<x>            authenticated user — enabled models for the agent dropdown
GET   /llm-models/available?provider=<x>&refresh=N    live + DB-fallback list (5-min cache)
GET   /admin/llm-models                   admin — all rows including disabled
POST  /admin/llm-models                   admin — add
PATCH /admin/llm-models/{id}              admin — update label / enabled
POST  /admin/llm-models/{id}/verify       admin — probe + stamp
DELETE /admin/llm-models/{id}             admin — delete
```

Verify probes the actual model with a 1-token completion (or `generateContent` for Google) using the configured API key. Status codes:

- `ok` → stamped, model stays enabled
- `unavailable` → stamped, **enabled flipped to false** (auto-disable)
- `auth_error` / `error` → no stamp change

`GET /llm-models/available` runs the same per-model probe across all 4 providers before returning so the dropdown only shows what the key can actually invoke.

## Admin (other)

```
GET    /admin/llm-keys                                  list (provider + prefix only)
PUT    /admin/llm-keys/{provider}                       upsert encrypted key
POST   /admin/llm-keys/{provider}/test-connection       provider auth check
DELETE /admin/llm-keys/{provider}                       delete

GET    /admin/users
POST   /admin/users/{id}/role                           body: {role}
DELETE /admin/users/{id}                                deactivate

GET    /admin/deployment-settings
GET    /admin/deployment-settings/{key}
PUT    /admin/deployment-settings/{key}                 body: {value}

GET    /admin/tools                                     built-in + MCP tool catalog
```

## Health

```
GET /health    →    {"status": "ok", "service": "composer", "version": "...", "environment": "..."}
GET /ready     →    {"status": "ready", "components": {"database": "ready", "schema": "current", "runtime": "ready", "telemetry": "ready"}}
```

`/health` is process liveness and returns 200 while the process responds. `/ready` checks the SQLAlchemy database and Alembic head with a one-second timeout, plus runtime initialization and draining state. Database or schema failures return 503. Telemetry exporter degradation is diagnostic and does not make the service unready. The readiness response contains only bounded component classifications.

## Error shapes

FastAPI HTTPException:
```json
{"detail": "uploaded file is empty"}
```

Pydantic validation (422):
```json
{"detail": [{"loc": ["body", "name"], "msg": "field required", "type": "value_error.missing"}]}
```

Composer's Python errors (`VectorDbNodeError`, `IfElseNodeError`, etc.) surface in the WebSocket as `node_failed` payloads with `error: "<ClassName>: <message>"`. The execution row's `error` column carries the same string.
