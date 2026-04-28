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
| `POST /api/run/{slug}` | configured (`rate_limit_api_run_per_minute`) | API key |
| `POST /uploads/extract-text` | 20 / min | user_id |

429s carry a `Retry-After` header.

## Workflows

```
GET    /workflows?mine={true|false}&isTemplate={true|false}&isPublic={true|false}&category=<x>&limit=N&offset=N
GET    /workflows/search?q=<query>&limit=N
GET    /workflows/{id}
POST   /workflows                                       (create)
PUT    /workflows/{id}                                  (update — owner or admin)
DELETE /workflows/{id}                                  (owner only — admins cannot)
PATCH  /workflows/{id}/owner                            (admin — reassign)
PATCH  /workflows/{id}/admin-flags                      (admin — set isPublic / isProduction / externalSlug)
```

`mine=true` always means "scope to the calling user's own workflows" regardless of role — admins asking for `mine=true` don't get the global feed (only `mine=false` or unset triggers admin-sees-all).

The `nodes` and `edges` arrays are validated against the discriminated union of node types defined in `src/engine/workflow.py`. Validation errors come back as `422` with FastAPI's standard `{detail: [{loc, msg, type}, ...]}` shape.

## Executions

```
POST /executions                                        body: {workflowId, input}
GET  /executions?workflowId=<id>&status=<x>&limit=N&offset=N
GET  /executions/{id}
POST /executions/{id}/resume                            body: {decision: "approved"|"rejected", note?: string}
WS   /executions/{id}/ws                                stream node events
```

`POST /executions` returns the new `executionId` immediately; the run continues in the background. Subscribe to the WebSocket for live updates or poll `GET /executions/{id}` for the final state.

Statuses: `running` / `waiting_approval` / `completed` / `failed` / `canceled`.

The WebSocket emits these event types:

```json
{"type": "node_started",     "executionId": "...", "payload": {"nodeId": "agent-1", "nodeName": "Answer Question", ...}}
{"type": "node_completed",   "executionId": "...", "payload": {"nodeId": "agent-1", "input": ..., "output": ..., "duration_ms": 1234}}
{"type": "node_failed",      "executionId": "...", "payload": {"nodeId": "agent-1", "error": "..."}}
{"type": "execution_completed", "executionId": "...", "payload": {"status": "completed", "output": ...}}
```

## External invoke

```
POST /api/run/{slug}                body: {input, sync?: boolean, timeoutSeconds?: number (max 300)}
```

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
```

`/auth/register` and `/auth/login` are only registered when `COMPOSER_DEPLOYMENT_MODE=standalone`. Embedded mode expects the parent IE app to mint JWTs that Composer validates.

## MCP servers

```
GET    /mcp-servers                       list (own + shared)
GET    /mcp-servers/{id}
POST   /mcp-servers                       register
PUT    /mcp-servers/{id}                  update
DELETE /mcp-servers/{id}                  owner-only
PATCH  /mcp-servers/{id}/shared           admin — flip isShared
PATCH  /mcp-servers/{id}/owner            admin — reassign
POST   /mcp-servers/{id}/test-connection  health check
GET    /mcp-oauth/callback                OAuth redirect URI (handled internally)
GET    /mcp-oauth/authorize-url/{id}      kick off OAuth flow
```

Six critical MCP fixes from OAB's April 2026 debugging session are encoded — RFC 8707 `resource` parameter on every OAuth grant, manual `tools/list` (skipping Anthropic's native MCP connector that 73K-char tool definitions broke), `inputSchema` (camelCase) tolerance, server-side token retrieval (tokens never transit the client), shared-server token fallback, explicit LangSmith config threading. See [`decisions.md`](decisions.md) ADR-0007–ADR-0010 for the full story.

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
```

Always returns 200 if the FastAPI process is responsive — does not check Postgres or LLM provider connectivity. For deeper checks, use the workflow execution path as a smoke test.

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
