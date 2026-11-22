# SQLAlchemy Migration Phase 2 — Integrations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every read and write of `mcp_servers`, `mcp_oauth_tokens`, `mcp_oauth_states` and `cloud_storage_connections` from Prisma to SQLAlchemy repository modules, with no change in behaviour except the deliberate fixes listed below.

**Architecture:** Same as phase 1: one repository module per area in `src/db/repos/`, returning frozen dataclasses, never committing. New in this phase: most of this code runs during workflow execution and does network I/O (MCP JSON-RPC, OAuth token endpoints, Google Drive). So the OAuth, resolver and Drive helpers take an `async_sessionmaker` and open **short sessions around database work only** — no session is ever open across a network call.

**Tech Stack:** SQLAlchemy 2 async + psycopg 3, PostgreSQL 15, FastAPI, pytest + pytest-asyncio + pytest-httpx, pyright strict, ruff.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md) (phase 2 row of §3.3; §2.1 rules; §5 testing). Phase 1 plan and its Outcome section (lessons this plan builds in): [`2026-11-20-sqlalchemy-phase-1-settings-and-admin.md`](2026-11-20-sqlalchemy-phase-1-settings-and-admin.md).

## Global Constraints

Everything in phase 1's Global Constraints still applies (repos are the only query code; callers own transactions; records not ORM objects; `utcnow()` for writes; explicit `updated_at` in ON CONFLICT `set_`; `add_or_already_exists` for inserts that can collide, never `session.add` first; repos imported as modules; no edits to `prisma/`, `pyproject.toml`, `uv.lock`, `.github/**`; commits authored by Balaji Rajan `<balajirajan@gmail.com>` ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). Additionally:

- **Short-session rule.** No SQLAlchemy session may be open across network I/O (MCP `initialize`/`tools/list`/`tools/call`, OAuth token POSTs, Google token refresh, Drive API, LLM calls). A session holds its pooled connection from the first query until commit/rollback/close; Prisma never did. Functions that interleave DB work and network calls open a fresh `async with sessionmaker() as s:` block for each DB step and close it before the next network call. Every such function gets a unit test proving the ordering (record session enter/exit events and network-call events; assert no network event falls inside an open session).
- **Out-of-request code** (executors, resolver, OAuth helpers, Drive helpers, internal pollers) takes an `async_sessionmaker[AsyncSession]` parameter in place of today's `db: Any`. Executors obtain it from `get_registered_sessionmaker()` (`src/db/runtime.py`); routes pass `request_sessionmaker(request)`. When none is attached: raise the same error today's `_require_db()` raises in executors, and **fail closed** in routes outside `ENVIRONMENT=development`.
- **The six MCP fixes in CLAUDE.md §1 must survive unchanged**, in particular #4 (tokens are fetched server-side, never sent to the client) and #5 (shared servers fall back to the creator's token when the caller has none). Behaviour tests pin both.
- **Secrets:** read responses expose only `hasAccessToken`/`hasOauthConfig`, redacted headers, and never `oauthConfig.clientSecret`, encrypted tokens, refresh tokens or code verifiers. Logs carry exception class names only.
- **CI parity:** before every commit also run `pyright --pythonplatform Linux --pythonversion 3.12 src tests` (phase 1's merge was blocked by a CI-only pyright error).
- **Coordination:** Codex owns `src/telemetry/**` on `feat/telemetry-foundation`. Don't touch it. `src/main.py`: only if unavoidable, and only lifespan-body lines.

## Decisions made while planning (2026-11-21)

| Topic | Decision |
|---|---|
| OAuth `state` consumption | **Fix:** today it is find → expiry check → delete, so two callbacks with the same state can both pass. It becomes one `DELETE … WHERE state = :s AND expires_at > :now RETURNING …`; the loser and an expired state get today's "invalid/expired state" error. Pinned by a race test. |
| Google Drive re-grant without a refresh token | **Keep** today's behaviour (the upsert writes `encrypted_refresh_token = NULL` when Google omits it). Like-for-like; logged as a follow-up. |
| MCP custom `headers` | **Keep** today's behaviour: stored (sensitive values encrypted) and returned redacted, but the MCP client never sends them (only `src/executors/http.py` decrypts headers). Pre-existing gap, logged as a follow-up, not fixed in a migration PR. |
| Migration CLI (`src/migration/writer.py`, `runner.py`, `reconcile.py`) | Ported for these four tables (area moves whole), via `one_shot_session`. Their workflow/execution writes stay on Prisma until phases 3–4. |
| `api/internal.py` `poll_file_triggers` | Only its `cloudstorageconnection` read moves; the per-node read uses a short session closed before the Drive calls. |

---

## File structure

```
src/db/repos/
  mcp_servers.py        NEW  McpServerRecord; get, list_visible, create, update fields, set_owner, set_shared, delete, delete_for_user, claim_orphans_by_email
  mcp_oauth.py          NEW  McpOAuthTokenRecord, McpOAuthStateRecord; token get/upsert/update/delete; state create, consume (atomic), reap_expired
  cloud_storage.py      NEW  CloudStorageConnectionRecord; get, list_for_user, upsert, update_tokens, delete_for_user
tests/integration/sqla_phase2/   NEW  API + service behaviour tests (written first, run on Prisma)
tests/integration/db/repos/      NEW  test_mcp_servers.py, test_mcp_oauth.py, test_cloud_storage.py
```
Callers converted: `src/api/mcp_servers.py`, `src/mcp/resolver.py`, `src/mcp/base.py` (only how it obtains tokens), `src/mcp/oauth.py`, `src/tools/registry.py`, `src/executors/mcp.py`, `src/executors/agent.py` (only the `db=` it passes to MCP resolution), `src/executors/download_pdf.py`, `src/api/cloud_storage_oauth.py`, `src/integrations/google_drive/oauth.py`, `src/api/internal.py` (one read), `src/api/test_cleanup.py` (four deletes), `src/migration/writer.py`, `src/migration/runner.py`, `src/migration/reconcile.py` (one update).

Column names: SQLAlchemy models (`src/db/models/mcp.py`, `cloud_storage.py`, already present from phase 0) use snake_case attributes; today's code reads camelCase Prisma attributes (`server.userId`, `row.encryptedAccessToken`). Records use snake_case; response models keep their camelCase aliases, so API JSON is unchanged.

---

### Task 1: Behaviour tests, proven on the Prisma code

These define "unchanged". They go in `tests/integration/sqla_phase2/`, use the `client`/`app` fixtures and SQLAlchemy only for setup/cleanup, and must pass on today's code. Copy phase 1's fixture approach from `tests/integration/sqla_phase1/conftest.py` (`phase1_env` → `phase2_env` applied before `create_app()`, `make_user` factory, `admin`/`member`/`second_member`, `bearer()`, `ISO`).

**Network is never real.** Use `pytest-httpx`'s `httpx_mock` for OAuth token endpoints and Google, and monkeypatch MCP transport at one seam (`src/mcp/client.py`'s JSON-RPC post, or `McpToolProvider.health_check`/`tools`) — pick the seam that does not change across the port and say which in the commit.

**Service-level seam.** The resolver and OAuth helpers are not HTTP endpoints, and their signature changes in this phase (`db` → `sessionmaker`). Put calls to them behind ONE adapter fixture in `conftest.py` (`resolve_tools(server_ids, user_id) -> list[str]`, `valid_access_token(server_id, user_id) -> str`). Task 3/4 may edit only that adapter's body; every test asserting through it stays unchanged.

Write these tests (each cleans up its own rows; names unique per run):

1. **MCP server CRUD** — create (bearer token + a sensitive header `Authorization` + a plain header + oauthConfig with clientSecret) → 201; the response has `hasAccessToken: true`, `hasOauthConfig: true`, the sensitive header redacted, the plain header verbatim, no `clientSecret`/token anywhere in the JSON (assert by searching the raw response text for the secret values). Read back from the DB: `encrypted_access_token` decrypts to the token; `oauth_config.clientSecret` is marked-encrypted (decrypts via `decrypt_marked`); the sensitive header is encrypted.
2. **Listing** — owner sees own private + all shared servers; second member sees only shared; ordering pinned exactly as today (check the current query: if unordered, assert set equality, not order).
3. **Get / delete / ownership** — 404 vs 403 for a non-owner exactly as today (read the route; pin status + detail); delete then get → 404.
4. **Share toggle and owner assignment** — admin assign by email and by id (404 details for unknown user), `isShared` toggle, effect on second member's listing.
5. **OAuth config update** — PUT of oauthConfig with the redacted/omitted clientSecret preserves the stored secret (check today's semantics and pin them).
6. **Test connection** — with the MCP seam returning two tools: response status fields, DB `connection_status`, `tools` JSON, `last_tested` set (strict ISO in the response), `last_error` cleared; with the seam raising: error status + `last_error` set (detail text pinned only by prefix if it embeds exception text).
7. **OAuth authorize → callback** — authorize returns a URL containing a `state`; a `mcp_oauth_states` row exists; callback with that state and an `httpx_mock`ed token endpoint (assert the token request body contains the RFC 8707 `resource` parameter — MCP fix #1) stores a token row (encrypted access + refresh); the state row is gone; **replaying the same state** → today's error status/detail; an **expired** state → same error.
8. **Disconnect** — delete OAuth token route → row gone; status code pinned.
9. **Shared-server token fallback (MCP fix #5)** — via the adapter: owner has a token for a shared server, second member has none → `valid_access_token(server, second_member)` returns the owner's token; when the second member connects their own, theirs wins.
10. **Token refresh** — via the adapter: token row with `expires_at` in the past and a refresh token; `httpx_mock`ed refresh endpoint (assert `resource` param present) → returns the new access token; DB row updated (new encrypted access token, new expiry, `updated_at` advanced — backdate first, as phase 1 did).
11. **Resolver** — via the adapter: resolving a server's tools returns the seam's tool names; a missing server id → today's behaviour (pin).
12. **Cloud storage** — Google callback with `httpx_mock`ed token exchange creates a connection (encrypted tokens; unique on user+provider+email; a second callback for the same account updates in place: same id, new token); `GET` connections lists the caller's only (second member sees none), provider filter works; picker token with an expired access token and mocked refresh returns the new token and updates the row; another user's connection id → today's status/detail.
13. **Cleanup** — the e2e cleanup endpoint removes a test user's servers, tokens, states and connections (assert all four tables empty for that user).

- [x] Write the conftest (fixtures, adapter, `phase2_env`) and the tests above.
- [x] Run on the unchanged code twice: `-m integration tests/integration/sqla_phase2` → all pass. Where today's behaviour differs from the expectation above, fix the TEST to match today (never the app) and list each case in the commit message.
- [x] Commit: `test(db): pin phase 2 integration behaviour on the Prisma code`.
- [x] **Review gate:** spec review on Opus (oracle strength: would a plausible port bug pass?), then fix, then re-review, before Task 2 starts.

---

### Task 2: MCP servers

**Files:** Create `src/db/repos/mcp_servers.py`, `tests/integration/db/repos/test_mcp_servers.py`. Modify `src/api/mcp_servers.py` (server routes; OAuth routes are Task 3), `src/mcp/resolver.py` (server reads only, signature → sessionmaker), `src/tools/registry.py`, `src/executors/mcp.py`, `src/executors/agent.py` (the MCP resolution call only), `src/mcp/base.py` (only if it reads the server record), `src/api/test_cleanup.py` (mcp_servers delete), `src/migration/writer.py` + `runner.py` (mcp server upsert), `src/migration/reconcile.py` (claim orphans).

**Interfaces (produce):**
```python
@dataclass(frozen=True)
class McpServerRecord:
    id: str; user_id: str | None; name: str; url: str; description: str | None; category: str | None
    auth_type: str; encrypted_access_token: str | None; header_name: str | None
    oauth_config: dict[str, Any] | None; tools: list[dict[str, Any]] | None
    connection_status: str; last_tested: datetime | None; last_error: str | None
    enabled: bool; is_official: bool; is_shared: bool; headers: dict[str, str] | None
    created_at: datetime; updated_at: datetime; original_owner_email: str | None

async def get(session, server_id: str) -> McpServerRecord | None
async def list_visible(session, user_id: str) -> list[McpServerRecord]          # user_id == :u OR is_shared; same order as today
async def create(session, *, values: McpServerCreate) -> McpServerRecord       # McpServerCreate: TypedDict of the insertable columns
async def update(session, server_id: str, changes: McpServerChanges) -> McpServerRecord | None   # TypedDict(total=False); bumps updated_at
async def delete(session, server_id: str) -> bool
async def delete_for_user(session, user_id: str) -> int
async def claim_orphans_by_email(session, email: str, user_id: str) -> int     # WHERE original_owner_email = :e AND user_id IS NULL
async def insert_if_absent(session, values: McpServerCreate) -> bool           # migration writer: find-then-create today; keep semantics
```
JSONB values are replaced, never mutated; pass plain dicts/lists (no Prisma `Json(...)` wrappers).

- [x] Repo tests first (real Postgres, rolled-back `session` fixture): CRUD, `list_visible` semantics, `update` only changes given keys and bumps `updated_at` (patched `utcnow`), JSON round trip of `oauth_config`/`tools`/`headers` (including `None` → SQL NULL), `claim_orphans_by_email` count and idempotence, `delete_for_user` count. Watch them fail; implement; pass.
- [x] Port the callers. Routes: `Depends(get_session)` for simple CRUD; `test_mcp_connection` follows the short-session rule — read the server in one short session, close it, run the MCP calls, then write the result in a second short session (unit test proves no network call happens inside an open session). Resolver/executors: `sessionmaker` parameter; executors pass `get_registered_sessionmaker()` and raise today's `_require_db()` error when it is None.
- [x] Convert unit tests (`tests/unit/api/test_mcp_servers.py`, `tests/unit/mcp/test_resolver*.py`, executor tests, migration tests) to patched repo functions + `FakeSessionmaker`/`override_session`; keep every behaviour assertion.
- [x] Verify: repo tests; converted unit tests; `tests/integration/sqla_phase2` unchanged and passing (except the adapter body); full unit suite; CI-parity pyright. Commit: `feat(db): MCP servers on SQLAlchemy (phase 2)`.

---

### Task 3: MCP OAuth tokens and states

**Files:** Create `src/db/repos/mcp_oauth.py`, `tests/integration/db/repos/test_mcp_oauth.py`. Modify `src/mcp/oauth.py` (all functions: signature `db` → `sessionmaker`), the OAuth routes in `src/api/mcp_servers.py` (authorize, callback, token delete/status), `src/mcp/base.py` (token acquisition path), `src/api/test_cleanup.py` (tokens/states deletes), `src/migration/writer.py` + `runner.py` (token insert).

**Interfaces (produce):**
```python
@dataclass(frozen=True)
class McpOAuthTokenRecord:
    id: str; mcp_server_id: str; user_id: str; encrypted_access_token: str
    encrypted_refresh_token: str | None; expires_at: datetime | None; scope: str | None
    token_type: str; created_at: datetime; updated_at: datetime

@dataclass(frozen=True)
class McpOAuthStateRecord:
    id: str; mcp_server_id: str; user_id: str; state: str; code_verifier: str
    redirect_uri: str; scope: str | None; expires_at: datetime; created_at: datetime

async def get_token(session, server_id: str, user_id: str) -> McpOAuthTokenRecord | None
async def upsert_token(session, server_id: str, user_id: str, values: TokenValues) -> McpOAuthTokenRecord
    # INSERT … ON CONFLICT (mcp_server_id, user_id) DO UPDATE; updated_at in set_; populate_existing
async def update_token(session, token_id: str, values: TokenValues) -> McpOAuthTokenRecord | None
async def delete_token(session, token_id: str) -> bool
async def delete_tokens_for_user(session, user_id: str) -> int
async def create_state(session, values: StateValues) -> McpOAuthStateRecord     # via add_or_already_exists (state is unique)
async def consume_state(session, state: str, now: datetime) -> McpOAuthStateRecord | None
    # DELETE FROM mcp_oauth_states WHERE state = :s AND expires_at > :now RETURNING *  — atomic, one winner
async def reap_expired_states(session, now: datetime) -> int
async def delete_states_for_user(session, user_id: str) -> int
async def insert_token_if_absent(session, values: …) -> bool                    # migration writer
```

- [x] Repo tests first: token upsert creates then updates in place (same id; `updated_at` deterministic; held-reference refresh test — ON CONFLICT needs `populate_existing`, prove it fails without), `consume_state` returns the row once and `None` on the second call, `None` for an expired state; **race test**: five concurrent `consume_state` calls on committed data → exactly one returns the row (copy phase 1's `test_concurrent_revocations_have_exactly_one_winner` shape, with cleanup).
- [x] Port `src/mcp/oauth.py` under the short-session rule: `build_authorize_url` (reap + create state in one short session), `exchange_code_for_tokens` (consume state in one short session → close → token POST → upsert token in a second short session), `refresh_token` (POST → short session update), `get_valid_access_token` (short-session read(s), including the creator-token fallback for shared servers → close → maybe refresh → short-session write). Each gets an ordering unit test. Keep the RFC 8707 `resource` parameter on every token request (MCP fix #1) and keep tokens server-side (fix #4).
- [x] Port the OAuth routes; `oauth_callback` has no auth today — keep that, and keep its error responses identical.
- [x] Convert unit tests (`tests/unit/mcp/test_oauth_*.py`, `tests/unit/api/test_mcp_servers_oauth.py`, `tests/regression/test_oab_highspot_oauth.py`, `tests/integration/test_mcp_highspot.py` only as far as signatures require).
- [x] Verify as in Task 2 (plus the race test). Commit: `feat(db): MCP OAuth tokens and states on SQLAlchemy (phase 2)` — body notes the atomic state consumption.

---

### Task 4: Cloud-storage connections

**Files:** Create `src/db/repos/cloud_storage.py`, `tests/integration/db/repos/test_cloud_storage.py`. Modify `src/api/cloud_storage_oauth.py`, `src/integrations/google_drive/oauth.py` (`get_valid_drive_access_token(connection_id, sessionmaker)`), `src/executors/download_pdf.py`, `src/api/internal.py` (`poll_file_triggers`' connection read only), `src/api/test_cleanup.py` (connections delete).

**Interfaces (produce):**
```python
@dataclass(frozen=True)
class CloudStorageConnectionRecord:
    id: str; user_id: str; provider: str; account_email: str; encrypted_access_token: str
    encrypted_refresh_token: str | None; expires_at: datetime | None; scope: str | None
    created_at: datetime; updated_at: datetime

async def get(session, connection_id: str) -> CloudStorageConnectionRecord | None
async def list_for_user(session, user_id: str, provider: str | None = None) -> list[CloudStorageConnectionRecord]   # same order as today
async def upsert(session, *, user_id: str, provider: str, account_email: str, values: ConnectionTokens) -> CloudStorageConnectionRecord
    # ON CONFLICT (user_id, provider, account_email); writes encrypted_refresh_token even when None (today's behaviour)
async def update_tokens(session, connection_id: str, values: ConnectionTokens) -> CloudStorageConnectionRecord | None
async def delete_for_user(session, user_id: str) -> int
```

- [x] Repo tests first (upsert in place, held-reference refresh test, deterministic `updated_at`, `list_for_user` scoping and provider filter).
- [x] Port under the short-session rule: `google_drive_callback` (token exchange first, then a short-session upsert), `get_picker_token` (short read → close → `get_valid_drive_access_token`), `get_valid_drive_access_token` (short read → close → refresh POST → short update), `poll_file_triggers` (per-node connection read in a short session closed before Drive calls; its workflow/execution Prisma work is untouched), `download_pdf` executor (sessionmaker from the registry). Ordering unit tests for each.
- [x] Convert unit tests (`tests/unit/api/test_cloud_storage_oauth.py`, `tests/unit/integrations/test_google_drive_oauth.py`, `tests/unit/api/test_internal_poll_file_triggers.py`, download-pdf executor tests, `tests/unit/api/test_test_cleanup.py`).
- [x] Verify as in Task 2. Commit: `feat(db): cloud-storage connections on SQLAlchemy (phase 2)`.

---

### Task 5: Lock, docs, CI, PR

- [x] Grow `MIGRATED_PATHS` in `tests/unit/db/test_prisma_import_boundary.py` with every file that now has no `prisma`/`src.storage` import (verify each; list exclusions with reasons). Done-criterion grep: `grep -rnE "\b(db|prisma)\.(mcpserver|mcpoauthtoken|mcpoauthstate|cloudstorageconnection)\." src` → empty.
- [x] CHANGELOG (`[Unreleased]`, first entry): the four tables now on SQLAlchemy; behaviour proven by `tests/integration/sqla_phase2`; deliberate changes (atomic OAuth state consumption; short sessions around network I/O; fail-closed outside development where applicable); kept-as-is items with follow-ups (Drive refresh-token wipe on re-grant; MCP custom headers never sent).
- [x] `docs/architecture.md`: extend the data-access section with the short-session rule for out-of-request code. Spec §3.3: phase 2 "(done, PR pending)". This plan: tick boxes and add an Outcome section.
- [ ] Run everything CI runs, including CI-parity pyright and `-m "integration and not live"` against a throwaway Postgres 15 (`docker run -d --rm --name composer-p2-pg …`, then `prisma migrate deploy` + `alembic upgrade head`). Delete the container when the phase is done.
- [ ] Commit; push; open the PR; wait for all six checks; merge only with Balaji's go-ahead (merging deploys to production).

## Follow-ups (not in this phase)
- ~~MCP custom `headers` are stored but never sent by `src/mcp/client.py`~~ **Done 2026-11-22** (branch `fix/integration-followups`): `McpToolProvider` sends them, sensitive values decrypted; client and auth headers win.
- ~~Google Drive re-grant without a refresh token overwrites the stored refresh token with NULL~~ **Done 2026-11-22** (branch `fix/integration-followups`): Drive and MCP upserts COALESCE the refresh token and scope.
- ~~Concurrent refreshes of one token can both POST the same refresh token~~ **Done 2026-11-22** (branch `fix/integration-followups`): serialized with a transaction-scoped advisory lock; documented short-session exception.

## Outcome

What changed while executing this plan:

- **PR #28 landed mid-phase.** The MCP visibility security fix (test-connection and OAuth authorize check `user_can_use_server`) merged while this phase was in progress, and two oracle tests were updated for the new 404 behaviour.
- **The oracle grew from 49 to 63 tests** (`tests/integration/sqla_phase2/`) after two Opus reviews found behaviour a plausible wrong port could break unnoticed.
- **Task 2 kept temporary dual Prisma/SQLAlchemy plumbing** for MCP servers so the OAuth code, still on Prisma, kept working. Task 3 removed it.
- **Task 4's review found a stale-token bug:** the Drive token helper returned an expired token when the connection vanished mid-refresh. It now raises `DriveConnectionMissingError` (the picker-token route answers 404), with a regression test.
- **Review follow-ups (commit "small phase 2 review follow-ups"):** the Drive OAuth popup gets its close page when the database is unavailable; one shared `require_sessionmaker` in `src/db/runtime.py`; `_post_token_form` shared by the Drive code exchange and refresh; a docstring rewrap in `download_pdf.py`; and a flaky phase 1 ordering test (`test_api_keys_list_newest_first`) given a wider gap between keys.
- **Follow-ups since done (2026-11-22, branch `fix/integration-followups`):** MCP custom headers are sent; re-grants keep the stored refresh token and scope; concurrent refreshes of one token are serialized by an advisory lock.
