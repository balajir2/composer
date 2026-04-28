# Phase 3b — MCP OAuth + the six hard-won fixes

**Target exit date:** 2026-04-21
**Author:** Composer team
**Supersedes:** Nothing (extends Phase 3a).
**Depends on:** [Phase 3a design](2026-04-20-phase-3a-mcp-infrastructure-design.md) and its commits (`16bb663`..`0c1bf07` on `main`).

---

## 1. Goal

Deliver OAuth MCP server support end-to-end: a user registers an OAuth MCP server (e.g., Highspot), goes through the browser authorize flow, and the Agent / `mcp` executor can then invoke tools on that server using the user's per-server access token. All tokens live server-side; none transit the client. Verified by a real integration test against Highspot.

While delivering OAuth, encode the remaining four of [OAB's six hard-won MCP fixes](../../../CLAUDE.md#1-the-six-critical-mcp-fixes-from-oabs-april-2026-debugging-session) that didn't apply to Phase 3a's static-auth path:

1. **RFC 8707 `resource` parameter** on authorize, token exchange, token refresh, and client_credentials grant — all four.
2. **Server-side OAuth token retrieval** — tokens never transit the client; `McpToolProvider` decrypts from Prisma at request time.
3. **`isShared` + service-account token fallback** — when a user can see a shared OAuth MCP but has no personal token for it, fall back to the owner's token.
4. **LangSmith config threaded explicitly** — pass a `LangSmithConfig` through `BuildContext` + `build_chat_model` rather than relying on `process.env`/env vars at runtime.

Phase 3a already delivered the other two fixes (manual tool calling, `inputSchema` camelCase).

## 2. Non-goals for this phase

- Multi-user admin dashboards for OAuth — frontend is Phase 10.
- Background token-rotation worker — refresh-on-use is sufficient for 3b; a background job is a future optimization.
- OAuth client secret rotation endpoints — manual re-register for now.
- OAuth token encryption-at-rest *key rotation* — out of scope for 3b; uses Phase 3a's single `ENCRYPTION_KEY`.
- Token-revocation-on-disconnect at the **provider** side — we only delete our stored row. Revoking the token at the IdP is best-effort (Highspot supports it; some IdPs don't). Document in the endpoint but don't hard-fail on revoke errors.

## 3. Success criteria

Phase 3b exits when:

1. All unit tests pass: `ruff`, `pyright --strict`, `pytest -m "not integration"` green on `main`.
2. Integration test against real Highspot passes end-to-end (register server → browser authorize → exchange → run Agent workflow using the MCP).
3. OAB's Highspot OAuth-lifecycle regression test (ported to pytest) passes.
4. `grep -rn "Phase 3b" src/ tests/` yields no `NotImplementedError("Phase 3b")` sentinels — all have been replaced with real implementations.
5. CHANGELOG, CLAUDE.md phase table, and `docs/design/decisions.md` all reflect 3b ✅ Complete, 4 ⏭ Next.

## 4. Architecture in one diagram

```
                                ┌──────────────────────┐
                                │  Browser (the user)  │
                                └──────────┬───────────┘
                                           │ 1. click "connect Highspot"
                                           ▼
                                ┌──────────────────────┐
                                │   Composer API       │
                                │ /mcp-servers/{id}/   │
                                │   oauth/authorize    │
                                └──────────┬───────────┘
                                           │ 2. build URL w/ resource+state+PKCE
                                           │    store McpOAuthState row
                                           ▼ 3. return {authorize_url}
                                ┌──────────────────────┐
                                │  Browser redirects   │
                                └──────────┬───────────┘
                                           │ 4. user signs in at IdP
                                           ▼
                                ┌──────────────────────┐
                                │ IdP (Highspot, etc.) │
                                └──────────┬───────────┘
                                           │ 5. 302 → our /oauth/callback?code=…
                                           ▼
                                ┌──────────────────────┐
                                │ GET /oauth/callback  │
                                │  - validate state    │
                                │  - POST token_url    │  ← fix #1: resource param
                                │    w/ resource+PKCE  │
                                │  - encrypt + store   │  ← fix #4: server-side only
                                │    McpOAuthToken     │
                                └──────────┬───────────┘
                                           │ 6. success page / JSON
                                           ▼
                                ┌──────────────────────┐
                                │   Browser: connected │
                                └──────────────────────┘

   Later, during workflow execution:

   Agent / mcp executor ──► resolve_tools_for_node ──► McpToolProvider(server)
                                                              │
                                                              │ ._build_auth_header()
                                                              ▼
                                                      oauth.get_valid_access_token(
                                                        server, user_id)
                                                              │
                                                              │  - look up McpOAuthToken
                                                              │    for (server, user)
                                                              │  - if missing AND isShared:
                                                              │     fall back to
                                                              │     McpOAuthToken
                                                              │     for (server, server.userId)
                                                              │     ← fix #5
                                                              │  - if near-expiry:
                                                              │     refresh (resource param)
                                                              │     ← fix #1
                                                              │  - decrypt + return plaintext
                                                              ▼
                                                      {"Authorization":
                                                       f"Bearer {token}"}
                                                              │
                                                              ▼
                                                      MCP tools/list → tools/call
```

Zero client knowledge of tokens. One round-trip per tool call in the hot path (token lookup is a single Prisma `find_unique`; refresh only fires near-expiry).

## 5. Prisma schema additions

One additive migration. `McpServer` stays untouched (its `oauthConfig Json?` and `isShared` columns were pre-provisioned in Phase 3a per ADR-0010).

```prisma
// ─── Phase 3b ────────────────────────────────────────────

model McpOAuthToken {
  id                    String    @id @default(cuid())
  mcpServerId           String    @map("mcp_server_id")
  userId                String    @map("user_id")
  encryptedAccessToken  String    @map("encrypted_access_token")
  encryptedRefreshToken String?   @map("encrypted_refresh_token")
  expiresAt             DateTime? @map("expires_at")
  scope                 String?
  tokenType             String    @default("Bearer") @map("token_type")
  createdAt             DateTime  @default(now()) @map("created_at")
  updatedAt             DateTime  @updatedAt       @map("updated_at")

  @@unique([mcpServerId, userId])
  @@map("mcp_oauth_tokens")
  @@index([mcpServerId])
  @@index([userId])
}

model McpOAuthState {
  id               String    @id @default(cuid())
  mcpServerId      String    @map("mcp_server_id")
  userId           String    @map("user_id")
  state            String    @unique
  codeVerifier     String    @map("code_verifier")  // PKCE
  redirectUri      String    @map("redirect_uri")
  scope            String?
  expiresAt        DateTime  @map("expires_at")  // 5 min from creation
  createdAt        DateTime  @default(now()) @map("created_at")

  @@map("mcp_oauth_states")
  @@index([state])
  @@index([expiresAt])
}
```

Rationale:

- `@@unique([mcpServerId, userId])` — one token per (server, user) pair. Re-authorizing *updates* rather than duplicating.
- `McpOAuthState` is short-lived bookkeeping for CSRF + PKCE. A small reaper (called lazily on any callback) removes expired rows.
- Both tokens encrypted at rest with 3a's `src/security/encryption.py`. Refresh token is optional because some IdPs don't issue one on certain grant types.
- No `resource` column — the `resource` value is derived at request time from `server.url`'s origin, not stored.

## 6. Module layout

```
src/mcp/
  oauth.py              NEW — OAuth primitives (authorize URL, token exchange,
                              refresh, get_valid_access_token w/ fallback).
                              All outbound calls include RFC 8707 `resource`.
  base.py               CHANGED — McpToolProvider.auth / _build_auth_header
                              wire up the OAuth path (no longer raises
                              NotImplementedError for authType=='oauth').
  resolver.py           UNCHANGED from 3a.
  client.py             UNCHANGED from 3a.
  schema_adapter.py     UNCHANGED from 3a.

src/api/
  mcp_servers.py        CHANGED — add /oauth/authorize, /oauth/callback,
                              /oauth/disconnect routes.

src/engine/
  langgraph_executor.py CHANGED — thread LangSmithConfig into ContextVar (fix #6).
  context.py            CHANGED — add langsmith_config ContextVar alongside db.

src/llm/
  providers.py          CHANGED — build_chat_model accepts optional
                              langsmith_config; wraps the returned model with
                              with_config when provided (fix #6).

src/tools/
  base.py               CHANGED — BuildContext gains
                              langsmith_config: LangSmithConfig | None = None.

src/executors/
  agent.py              CHANGED — pass langsmith_config from context into
                              build_chat_model.

prisma/schema.prisma    CHANGED — append McpOAuthToken + McpOAuthState models.

tests/
  unit/mcp/test_oauth.py              NEW — PKCE, state, URL builder, exchange,
                                            refresh, fallback (resource param
                                            asserted on every outbound call).
  unit/api/test_mcp_servers_oauth.py  NEW — three new endpoints, happy path +
                                            state-mismatch + expired-state.
  unit/engine/test_langsmith_config.py NEW — ContextVar + threading.
  integration/test_mcp_highspot.py    NEW — real Highspot OAuth flow end-to-end.
  regression/test_oab_highspot_oauth.py NEW — OAB regression port.
```

## 7. OAuth primitives — `src/mcp/oauth.py`

Stateless module. Every function takes a Prisma client explicitly (no imports of engine context — keeps `oauth.py` testable with a mock DB in unit tests).

### 7.1 PKCE + state

```python
def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge_s256).
    Verifier: 43-128 chars URL-safe; we use 64. Challenge: SHA-256(verifier), base64url no-padding.
    """

def generate_state(nbytes: int = 32) -> str:
    """URL-safe random state for CSRF protection."""
```

Both use `secrets` module. `code_challenge_method` is always `S256` (no `plain`).

### 7.2 Resource derivation

```python
def derive_resource(server_url: str) -> str:
    """RFC 8707 requires resource = scheme://host (no path, no query).
    Highspot rejects token exchange if resource differs from the MCP URL's origin.
    """
```

Applied uniformly across all four OAuth flows (authorize, token exchange, refresh, client_credentials).

### 7.3 Authorize URL

```python
async def build_authorize_url(
    server: Any,               # Prisma McpServer row
    user_id: str,
    redirect_uri: str,
    db: Any,
) -> str:
    """
    1. Generate state + PKCE pair.
    2. Insert McpOAuthState row (expires_at = now + 5min).
    3. Build URL with query params:
       - client_id (from server.oauthConfig['clientId'])
       - redirect_uri
       - response_type=code
       - state
       - code_challenge, code_challenge_method=S256
       - scope (from server.oauthConfig['scopes'], space-joined)
       - resource = derive_resource(server.url)   ← fix #1
    4. Return the full URL.
    """
```

### 7.4 Token exchange

```python
async def exchange_code_for_tokens(
    server: Any,
    code: str,
    state: str,
    db: Any,
) -> McpOAuthToken:
    """
    1. Find McpOAuthState by state string; fail if missing or expired.
    2. Delete the state row (one-shot use).
    3. POST to server.oauthConfig['tokenUrl'] with form data:
       - grant_type=authorization_code
       - code
       - redirect_uri (must match the one stored in state row)
       - code_verifier (from state row)
       - client_id, client_secret (from oauthConfig; client_secret decrypted)
       - resource = derive_resource(server.url)   ← fix #1
    4. Parse response (access_token, refresh_token?, expires_in?, scope?).
    5. Encrypt tokens, upsert into McpOAuthToken keyed on (mcpServerId, userId).
    6. Return the new/updated row.
    """
```

### 7.5 Refresh

```python
async def refresh_token(
    server: Any,
    token_row: McpOAuthToken,
    db: Any,
) -> McpOAuthToken:
    """
    POST to tokenUrl with:
      - grant_type=refresh_token
      - refresh_token (decrypted from token_row)
      - client_id, client_secret
      - resource = derive_resource(server.url)    ← fix #1
    Update token_row with new encrypted access_token, new refresh_token if returned,
    new expires_at. Return updated row.
    """
```

### 7.6 The front-door: `get_valid_access_token`

```python
async def get_valid_access_token(
    server: Any,
    user_id: str | None,
    db: Any,
    *,
    expiry_buffer_seconds: int = 60,
) -> str:
    """
    1. Try to find McpOAuthToken for (server.id, user_id).
    2. If missing:
       - If server.isShared is True and user_id != server.userId:
            try (server.id, server.userId) — the service-account fallback.  ← fix #5
       - Else raise McpTokenMissingError (caller handles as "user must reconnect").
    3. If token.expiresAt and now >= token.expiresAt - buffer:
       - If encryptedRefreshToken present, refresh_token(...) and use result.
       - Else raise McpTokenExpiredError.
    4. Return the decrypted access_token (str, plaintext).
    """
```

### 7.7 Client-credentials (optional; behind a flag)

A few IdPs require an additional machine-to-machine grant. Supported but off by default. If `server.oauthConfig['clientCredentialsFlow'] == True`, `build_authorize_url` is replaced with a direct `client_credentials` POST (also with `resource` — fix #1 applies here too).

## 8. `McpToolProvider` OAuth branch

Currently in Phase 3a, `base.py` does:

```python
if auth_type == "oauth":
    raise NotImplementedError("OAuth auth for MCP lands in Phase 3b")
```

…in three places: the `auth` property, `_build_auth_header`, and the `__init__` (which conditionally skips `_build_auth_header` when oauth). After 3b:

```python
# auth property
if auth_type == "oauth":
    config = self._server.oauthConfig or {}
    return OAuthAuth(
        authorize_url=config.get("authorizeUrl", ""),
        token_url=config.get("tokenUrl", ""),
        scopes=config.get("scopes", []),
        include_rfc8707_resource=True,  # always True for Composer
    )

# _build_auth_header — now requires a db + user_id. Two options:
#   (a) Attach db + user_id on the McpToolProvider instance at resolver time
#       and read them here. Same pattern as `self._server`.
#   (b) Accept db + user_id as args to _build_auth_header(db, user_id) and
#       have the provider expose a build_client(db, user_id) method that
#       constructs the MCPClient with the right auth header.
#
# We pick (a): resolver passes db + user_id to __init__, stored as private
# attrs, used by _build_auth_header. Simpler call sites; tests already
# construct providers with a duck-typed row, so adding two kwargs is cheap.
```

### 8.1 Revised `McpToolProvider.__init__`

```python
def __init__(
    self,
    server: Any,
    *,
    db: Any | None = None,      # required for OAuth auth
    user_id: str | None = None, # required for OAuth auth
) -> None:
    self._server = server
    self._db = db
    self._user_id = user_id
    resolved_url = substitute_url_placeholders(server.url)
    # For OAuth we can't build the auth header synchronously because it needs
    # a Prisma query + possible refresh. Create MCPClient lazily via an
    # auth-header factory instead.
    auth_header_factory = self._make_auth_header_factory()
    self._client = MCPClient(resolved_url, auth_header_factory=auth_header_factory)
```

### 8.2 Revised `MCPClient`

`MCPClient.__init__` gains an optional `auth_header_factory: Callable[[], Awaitable[dict[str, str] | None]]` argument. When present, each `_rpc` call awaits the factory *immediately before sending* to get a fresh header (so OAuth's refresh-on-use just works). The old `auth_header` dict path stays for static-auth simplicity.

This is the one breaking change to 3a's client shape, but it's additive and unit-tested.

### 8.3 `_build_auth_header` OAuth path

```python
async def _build_auth_header_async(self) -> dict[str, str]:
    auth_type = self._server.authType
    if auth_type == "none":
        return {}
    if auth_type in {"api-key", "bearer"}:
        # (3a path, unchanged — just synchronous. Wrap in async here.)
        return self._build_static_auth_header()
    if auth_type == "oauth":
        if self._db is None:
            raise RuntimeError(
                f"McpToolProvider for oauth server {self._server.id!r} needs db+user_id; "
                f"resolver must pass them."
            )
        from src.mcp.oauth import get_valid_access_token
        token = await get_valid_access_token(
            self._server, self._user_id, self._db
        )
        return {"Authorization": f"Bearer {token}"}
    raise ValueError(f"Unknown authType {auth_type!r}")
```

### 8.4 Resolver changes

Two small changes to `src/mcp/resolver.py`:

1. `resolve_mcp_tools_for_node` already receives `db`; pass `db + user_id` into `McpToolProvider(server, db=..., user_id=...)`.
2. `resolve_single_mcp_tool` already receives `db + user_id`; same change.

No permission-check changes — 3a's owner + isShared logic continues to work. The service-account fallback for *tokens* happens inside `get_valid_access_token`, not in the resolver.

## 9. REST endpoints

### 9.1 `POST /mcp-servers/{server_id}/oauth/authorize`

Request body:
```json
{ "redirectUri": "http://localhost:3000/oauth/callback" }
```

Response:
```json
{ "authorizeUrl": "https://highspot.com/oauth/authorize?..." }
```

The caller (frontend) redirects the user's browser to `authorizeUrl`. The response includes the resolver-computed URL with `resource`, `state`, PKCE challenge, etc.

Pre-conditions:
- Server must exist
- Server must have `authType == 'oauth'`
- `server.oauthConfig` must contain `authorizeUrl`, `tokenUrl`, `clientId`, and (decrypted) `clientSecret`
- Caller must be owner OR server must be shared (permission reuses 3a logic)

### 9.2 `GET /oauth/callback?code=…&state=…`

Not nested under `/mcp-servers/` because OAuth providers expect a stable redirect URI.

Flow:
1. Look up state row; 400 if missing or expired.
2. Look up the associated `McpServer` (state row carries `mcpServerId`).
3. Call `oauth.exchange_code_for_tokens(...)` which handles the token POST + store.
4. Return a small HTML success page (dev) or JSON `{ok: true, serverId: "..."}` (configurable via `Accept` header).

### 9.3 `POST /mcp-servers/{server_id}/oauth/disconnect`

Deletes the caller's `McpOAuthToken` row for this server. Best-effort IdP-side revocation if `server.oauthConfig['revocationUrl']` is set (POST token + `client_id/secret` + resource). Never fails the request on revocation error — delete the local row regardless.

Returns `204 No Content`.

### 9.4 Endpoint authorization

All three use 3a's anonymous `user_id='dev'` default (ADR-0005 still applies). Phase 7 wires real auth middleware.

## 10. LangSmith config threading (fix #6)

### 10.1 Problem statement

Today `src/llm/providers.py build_chat_model` doesn't pass a LangSmith config into the returned model. LangChain's LangSmith integration picks it up from env vars (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT`, etc.). This is what broke OAB in production: some worker context cleared env vars and tracing silently vanished, with no test catching it. OAB fixed it by passing a config dict explicitly through the execution path.

### 10.2 Design

Introduce a tiny dataclass and a ContextVar.

```python
# src/engine/context.py  (extended)

from dataclasses import dataclass

@dataclass(frozen=True)
class LangSmithConfig:
    tracing_v2: bool
    project: str
    endpoint: str
    api_key: str  # kept out of logs; not a secret-at-rest concern for this pass

_current_langsmith: ContextVar[LangSmithConfig | None] = ContextVar(
    "_current_langsmith", default=None
)

def set_current_langsmith(config: LangSmithConfig | None) -> None: ...
def get_current_langsmith() -> LangSmithConfig | None: ...
```

`LangGraphExecutor.run` builds a `LangSmithConfig` from Settings once and calls `set_current_langsmith(config)` alongside `set_current_db(self.db)`.

`src/llm/providers.py build_chat_model` accepts an optional `langsmith_config: LangSmithConfig | None = None`. When present:

```python
chat_model = _dispatch_by_provider_prefix(model_str)
if langsmith_config and langsmith_config.tracing_v2:
    chat_model = chat_model.with_config({
        "metadata": {"langsmith_project": langsmith_config.project},
        "tags": ["composer"],
    })
return chat_model
```

`AgentExecutor.arun` reads `get_current_langsmith()` and passes it to `build_chat_model`. No env-var reads at runtime.

### 10.3 Back-compat

When `langsmith_config` is None, behavior is unchanged (the current env-var-driven path still works — useful for ad-hoc scripts and tests). The *production* path through `LangGraphExecutor` always sets it.

## 11. Error model

New exceptions in `src/mcp/oauth.py`:

- `OAuthError(RuntimeError)` — base.
- `InvalidStateError(OAuthError)` — callback state not found / expired.
- `TokenExchangeError(OAuthError)` — IdP rejected the code exchange.
- `TokenRefreshError(OAuthError)` — IdP rejected refresh.
- `McpTokenMissingError(OAuthError)` — user has no token and no fallback is available. Callers surface as "user must reconnect".
- `McpTokenExpiredError(OAuthError)` — no refresh token available; user must re-authorize.

REST endpoints translate these to:
- `400` for `InvalidStateError`
- `401` for `McpTokenMissingError` / `McpTokenExpiredError`
- `502` for `TokenExchangeError` / `TokenRefreshError` (upstream IdP failure)

Inside the Agent's hot path, a token-expired-without-refresh error bubbles as the tool call's error; the Agent surfaces it to the LLM as a `ToolMessage` error rather than crashing the workflow. This keeps long-running workflows robust to occasional token expiry mid-execution.

## 12. Test plan

### 12.1 Unit

`tests/unit/mcp/test_oauth.py` (~18 tests):

- PKCE: verifier length + charset; challenge is correct S256 base64url.
- State: uniqueness, URL-safe, correct length.
- `derive_resource`: strips path + query, preserves scheme + host + port.
- `build_authorize_url`: inserts state row with correct TTL; URL has all 7 required params including `resource`; scopes joined correctly.
- `exchange_code_for_tokens`: state row validation + one-shot deletion; POST body has `resource` (**fix #1 assertion**); tokens encrypted; row upserted on re-authorization.
- `refresh_token`: POST body has `resource`; expiresAt updated; new refresh token persisted if returned.
- `get_valid_access_token`:
  - owner-with-token → returns decrypted token
  - near-expiry + has refresh → triggers refresh
  - near-expiry + no refresh → raises McpTokenExpiredError
  - non-owner + server is shared + no personal token → **service-account fallback to owner's token** (fix #5 assertion)
  - non-owner + server not shared + no personal token → raises McpTokenMissingError

`tests/unit/api/test_mcp_servers_oauth.py` (~8 tests):

- POST /authorize: returns URL, inserts state row
- POST /authorize on non-oauth server: 422
- GET /callback: happy path → inserts token row, returns 200
- GET /callback with bad state: 400
- GET /callback with expired state: 400
- POST /disconnect: deletes token row; returns 204
- POST /disconnect with IdP revoke error: still returns 204 (best-effort)
- POST /disconnect when no token exists: 204 idempotent

`tests/unit/engine/test_langsmith_config.py` (~4 tests):

- default ContextVar is None
- set/get round-trip
- context isolation across async contexts
- `build_chat_model` with a config wraps the model via `with_config`

### 12.2 Integration (real Highspot)

`tests/integration/test_mcp_highspot.py`:

Real OAuth flow. Skipped unless `HIGHSPOT_OAUTH_CLIENT_ID`, `HIGHSPOT_OAUTH_CLIENT_SECRET`, and `HIGHSPOT_OAUTH_REFRESH_TOKEN` are set.

Because a full browser-driven OAuth flow is hard to automate in a pytest run, the test takes a shortcut used by OAB: **seed a McpOAuthToken row directly** using a pre-obtained refresh token, then exercise the refresh → tool call path. The "full happy path" through `/oauth/authorize` + `/oauth/callback` is exercised by the unit test with a mock IdP; the integration test exercises the *post-authorization* hot path against a real IdP.

Steps:
1. Register Highspot MCP server (authType='oauth', with oauthConfig carrying Highspot's real authorize/token URLs + client id/secret).
2. Insert `McpOAuthToken` row with a known-valid refresh token (access token can even be empty — refresh fires immediately).
3. Run an Agent workflow using the Highspot MCP (answer a question about content using Highspot's `search_content` tool or similar).
4. Assert execution completes; assert the token row's `updatedAt` advanced (refresh fired).
5. Clean up.

### 12.3 Regression (OAB port)

`tests/regression/test_oab_highspot_oauth.py`:

Port of OAB's `highspot-oauth-lifecycle.spec.ts` into pytest. The OAB test exercises the same path and asserts the `resource` parameter appears on the token exchange request (OAB caught its missing-resource bug by literally asserting this). Composer's regression does the same — it mocks the token endpoint and asserts the POST body.

## 13. Phase-exit checklist

- [ ] All unit tests green: ruff + pyright + `pytest -m "not integration"`.
- [ ] Unit test of `oauth.py` asserts `resource` on all four OAuth flows (authorize URL query + 3 POST bodies).
- [ ] Integration test against real Highspot passes.
- [ ] OAB Highspot OAuth regression passes.
- [ ] No `NotImplementedError("Phase 3b")` remaining in the codebase.
- [ ] `src/engine/context.py` has `LangSmithConfig` + setter/getter, wired from `LangGraphExecutor`.
- [ ] `src/llm/providers.py build_chat_model` accepts and uses `langsmith_config`.
- [ ] CHANGELOG has Phase 3b section with fixes-scorecard.
- [ ] CLAUDE.md phase table: 3b ✅ Complete, 4 ⏭ Next.
- [ ] `docs/design/decisions.md` contains ADR-0011 (OAuth tokens server-side + service-account fallback).

## 14. Risks and mitigations

**Risk 1:** Highspot's OAuth quirks (resource-param-required, token-endpoint-audience, etc.) surface only during real testing. **Mitigation:** the integration test runs against real Highspot during phase-exit; the unit test explicitly asserts `resource` on all four flows.

**Risk 2:** Token refresh races — two concurrent Agent iterations hitting the same near-expiry token could both trigger refresh. **Mitigation:** 3b uses refresh-on-use with a 60-second expiry buffer. Two racing refreshes are harmless in practice (Highspot, like most IdPs, returns a new valid token on each refresh; the later write overwrites the earlier). A proper lock is deferred — if it becomes a real problem we add `SELECT ... FOR UPDATE`.

**Risk 3:** `McpOAuthState` rows accumulate if callbacks never fire. **Mitigation:** every `exchange_code_for_tokens` call runs a tiny reaper first (`DELETE FROM mcp_oauth_states WHERE expires_at < now()`). Bounded cost, no cron needed.

**Risk 4:** LangSmith config threading creates surprising behavior when mixed with env-var-set `LANGCHAIN_TRACING_V2=true`. **Mitigation:** when `langsmith_config` is provided, it wins; env vars are a fallback only when the config is None. Documented in `src/llm/providers.py` docstring.

**Risk 5:** Browser-automated OAuth flow is brittle for CI. **Mitigation:** as documented in §12.2, we seed a token row for the integration test rather than automating the browser. The full `/authorize` + `/callback` path is proven by unit tests with mock IdP.

## 15. New ADR

### ADR-0011: OAuth tokens are server-side + service-account fallback for shared servers

**Status.** Accepted.
**Date.** 2026-04-20.

**Context.** Phase 3b adds OAuth MCP servers. A user registers a server, connects via the browser flow, then the Agent invokes tools using the user's token. Several decisions had to be made atomically:

1. Where do tokens live? Client-side (e.g., browser localStorage) or server-side (Prisma)?
2. What happens when a user can see a **shared** OAuth MCP (visible to the org) but has no personal token for it?
3. Do we encode the RFC 8707 `resource` parameter everywhere it's required, or only where a specific IdP demands it?

**Decision.**

1. **Tokens live server-side only, encrypted with `src/security/encryption.py`.** The API response shape never includes ciphertext or plaintext tokens — only `hasAccessToken: bool`. The client triggers authorize / disconnect via REST; the server exchanges, stores, refreshes, and decrypts on demand inside `McpToolProvider._build_auth_header_async`. OAB learned this the hard way when debugging why mobile auth tokens were being logged to localStorage by a well-meaning but misguided refactor.

2. **Service-account token fallback for shared servers.** When a user accesses a shared MCP but has no personal `McpOAuthToken` row for it, `oauth.get_valid_access_token` falls back to the server *owner's* token. This lets teams register one OAuth MCP per org and share it without requiring every user to go through their own OAuth flow. The owner's token is the de-facto service account. Permissions on the *MCP* side still constrain what the shared token can do — this fallback only changes *which* token is sent.

3. **RFC 8707 `resource` parameter everywhere.** Every outbound OAuth call (authorize URL, token exchange, refresh, client_credentials) includes `resource = derive_resource(server.url)`. Unit tests assert this on all four paths. Highspot rejects token exchange without it; other IdPs may start enforcing it in the future.

**Alternatives considered.**

- **Client-side tokens via encrypted cookies.** Rejected — the client becomes a token custody point, tokens appear in logs, cross-site scripting becomes a token-leak vector.
- **Per-user tokens only (no fallback).** Rejected — forces every user in a team to OAuth-connect to every MCP. Teams with large rosters find this painful; OAB's telemetry showed under-use of shared MCPs as a result.
- **Emit `resource` only for Highspot.** Rejected — the IdP list changes over time; hard-coding provider sniffing is a maintenance trap. One path, one rule.

**Consequences.**

- `McpToolProvider` grows a `db + user_id` dependency (injected at resolver time). The resolver already has these, so the change is local.
- The UI can't inspect stored tokens even via dev tools — by design. Debugging a broken connection requires looking at `McpOAuthToken.expiresAt` + `lastError`-equivalent fields (not in 3b; add in Phase 10 UI).
- The service-account fallback creates an implicit delegation. Documented in the endpoint's OpenAPI description and in `oauth.get_valid_access_token`'s docstring.

**Implemented by.** Phase 3b (commits TBD).

## 16. Execution handoff

After this spec is approved and committed, the plan (`docs/superpowers/plans/2026-04-20-phase-3b-mcp-oauth-plan.md`) is written and executed via `superpowers:subagent-driven-development`, following the same cadence as Phases 1, 2, and 3a. The plan includes ~13 tasks with one commit each; all pass `ruff`, `pyright --strict`, and `pytest -m "not integration"` per commit; integration and regression tests run at phase-exit against real Neon + real Highspot; final commit updates CHANGELOG + CLAUDE.md + ADR-0011 `Implemented by`.
