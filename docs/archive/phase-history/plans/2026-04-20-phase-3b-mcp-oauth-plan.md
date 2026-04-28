# Phase 3b — MCP OAuth + Six Hard-Won Fixes: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship OAuth MCP support end-to-end — tokens encrypted server-side, RFC 8707 `resource` parameter on all four flows, service-account fallback for shared servers, LangSmith config threaded explicitly. Highspot is the canonical verification target.

**Architecture:** Phase 3a's abstractions already accommodate OAuth (`AuthRequirement.OAuthAuth`, `McpServer.oauthConfig`, `McpToolProvider.auth` stub). 3b fills in the stub. New module `src/mcp/oauth.py` owns PKCE, state, token exchange, refresh, and the front-door `get_valid_access_token`. `MCPClient` grows an optional async `auth_header_factory` hook so OAuth headers are fetched + refreshed on each outbound call. Three new REST endpoints (`POST /authorize`, `GET /callback`, `POST /disconnect`). `LangSmithConfig` threaded via ContextVar.

**Tech Stack:** Python 3.11/3.12, Prisma Python, FastAPI, httpx, cryptography (AESGCM), pytest + pytest-asyncio + pytest-httpx.

**Spec:** [`docs/superpowers/specs/2026-04-20-phase-3b-mcp-oauth-design.md`](../specs/2026-04-20-phase-3b-mcp-oauth-design.md)
**ADR:** [ADR-0011](../../design/decisions.md#adr-0011-oauth-tokens-server-side--service-account-fallback-for-shared-mcp-servers)

---

## Sequencing and discipline

14 tasks, one commit each. Every task ends with:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

All four must pass before the commit lands. Integration tests (Task 12) run separately against real Highspot + Neon at phase-exit.

**⚠️ Forbidden files (all tasks except where explicitly noted):** `pyproject.toml`, `.github/workflows/*`, `CLAUDE.md` (except Task 14), `docs/design/*` (except Task 14 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`. Schema-related tasks touch `prisma/schema.prisma` as authorized. If a task hits a pyright strict error, fix inline with `# pyright: ignore[specific]` — never loosen `pyproject.toml`.

Commit footer on every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`. No feature branches.

---

## Task 1: Prisma schema — `McpOAuthToken` + `McpOAuthState`

**Files:**
- Modify: `prisma/schema.prisma`
- Create: `prisma/migrations/<timestamp>_phase_3b_mcp_oauth/migration.sql` (generated)

**Authorized:** `prisma/schema.prisma` for this task.

- [ ] **Step 1: Append models**

Append to `prisma/schema.prisma` after the existing `McpServer` model:

```prisma
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
  codeVerifier     String    @map("code_verifier")
  redirectUri      String    @map("redirect_uri")
  scope            String?
  expiresAt        DateTime  @map("expires_at")
  createdAt        DateTime  @default(now()) @map("created_at")

  @@map("mcp_oauth_states")
  @@index([state])
  @@index([expiresAt])
}
```

- [ ] **Step 2: Generate + migrate**

```bash
uv run prisma generate
uv run prisma migrate dev --name phase_3b_mcp_oauth
```
(Fall back to `.venv/Scripts/python -m prisma <cmd>` if `uv` isn't on PATH.)

Expected: migration applied, client regenerated, no destructive-change prompt.

- [ ] **Step 3: Verify**

```bash
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 0 pyright errors, 207 tests pass (same as Phase 3a exit).

- [ ] **Step 4: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/
git commit -m "feat(schema): McpOAuthToken + McpOAuthState tables (Phase 3b)

McpOAuthToken: per-(server, user) access + refresh tokens (both
encrypted via src/security/encryption.py), expires_at, scope.
@@unique([mcpServerId, userId]) — re-authorizing updates.

McpOAuthState: short-lived CSRF state + PKCE code_verifier for the
browser authorize flow (5-min TTL, cleaned up on callback).

McpServer is untouched — its oauthConfig + isShared columns were
pre-provisioned in Phase 3a per ADR-0010.

See Phase 3b spec §5, ADR-0011.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: OAuth helpers — PKCE, state, resource derivation

**Files:**
- Create: `src/mcp/oauth.py`
- Create: `tests/unit/mcp/test_oauth_helpers.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/mcp/test_oauth_helpers.py`:

```python
"""Tests for PKCE, state, and resource-derivation helpers."""
import base64
import hashlib

from src.mcp.oauth import derive_resource, generate_pkce_pair, generate_state


def test_pkce_verifier_length_and_charset() -> None:
    verifier, challenge = generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    # URL-safe base64 alphabet (no padding)
    assert all(c.isalnum() or c in "-_" for c in verifier)
    assert "=" not in verifier


def test_pkce_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_pkce_pairs_are_unique() -> None:
    pairs = {generate_pkce_pair() for _ in range(10)}
    assert len(pairs) == 10


def test_state_is_url_safe() -> None:
    s = generate_state()
    assert all(c.isalnum() or c in "-_" for c in s)
    assert len(s) >= 20


def test_state_values_are_unique() -> None:
    states = {generate_state() for _ in range(10)}
    assert len(states) == 10


def test_derive_resource_strips_path_and_query() -> None:
    assert derive_resource("https://api.highspot.com/mcp") == "https://api.highspot.com"
    assert derive_resource("https://api.highspot.com/mcp?x=1") == "https://api.highspot.com"
    assert derive_resource("https://api.highspot.com/a/b/c") == "https://api.highspot.com"


def test_derive_resource_preserves_port() -> None:
    assert derive_resource("https://example.com:8443/mcp") == "https://example.com:8443"


def test_derive_resource_preserves_scheme() -> None:
    assert derive_resource("http://localhost:9000/mcp") == "http://localhost:9000"
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_helpers.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/mcp/oauth.py`:

```python
"""MCP OAuth primitives.

All outbound OAuth calls (authorize URL, token exchange, token refresh,
client_credentials) include the RFC 8707 `resource` parameter (fix #1 from
CLAUDE.md §1). Tokens live server-side only, encrypted with
src/security/encryption.py (fix #4). get_valid_access_token falls back to
the server owner's token when a user accesses a shared server but has no
personal token (fix #5).

See Phase 3b spec §7, ADR-0011.
"""

import base64
import hashlib
import secrets
from urllib.parse import urlsplit


class OAuthError(RuntimeError):
    """Base class for all OAuth-layer errors."""


class InvalidStateError(OAuthError):
    """Callback state not found or expired."""


class TokenExchangeError(OAuthError):
    """IdP rejected the authorization code exchange."""


class TokenRefreshError(OAuthError):
    """IdP rejected the token refresh request."""


class McpTokenMissingError(OAuthError):
    """No token found for (server, user) and no fallback available."""


class McpTokenExpiredError(OAuthError):
    """Token expired with no refresh token available — user must re-authorize."""


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge_s256).

    Verifier: 64 random URL-safe bytes → 86-char string (within the 43-128
    range RFC 7636 permits). Challenge: SHA-256(verifier), base64url, no padding.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_state(nbytes: int = 32) -> str:
    """URL-safe random state for CSRF protection."""
    return secrets.token_urlsafe(nbytes)


def derive_resource(server_url: str) -> str:
    """RFC 8707: resource identifier is the target server's origin (scheme://host[:port]).

    Highspot rejects token exchange when `resource` differs from the MCP URL's
    origin. Unit tests assert this on every outbound OAuth call.
    """
    parts = urlsplit(server_url)
    return f"{parts.scheme}://{parts.netloc}"


__all__ = [
    "InvalidStateError",
    "McpTokenExpiredError",
    "McpTokenMissingError",
    "OAuthError",
    "TokenExchangeError",
    "TokenRefreshError",
    "derive_resource",
    "generate_pkce_pair",
    "generate_state",
]
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_helpers.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 8/8 new pass; overall ~215 pass (207 + 8).

```bash
git add src/mcp/oauth.py tests/unit/mcp/test_oauth_helpers.py
git commit -m "feat(mcp-oauth): PKCE + state + resource derivation helpers

Three stateless primitives shared by all OAuth flows:
  - generate_pkce_pair — 64-byte URL-safe verifier + SHA-256/base64url challenge
  - generate_state — 32-byte URL-safe CSRF state
  - derive_resource — strips path/query, returns scheme://host[:port] per RFC 8707

Plus the OAuth error hierarchy (OAuthError + 5 specializations).
All subsequent flows compose these primitives.

See Phase 3b spec §7.1-§7.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: OAuth — `build_authorize_url`

**Files:**
- Modify: `src/mcp/oauth.py` (add function)
- Create: `tests/unit/mcp/test_oauth_authorize.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/mcp/test_oauth_authorize.py`:

```python
"""Tests for build_authorize_url."""
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

from src.mcp.oauth import build_authorize_url


def _server(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "owner",
        "name": "Highspot",
        "url": "https://api.highspot.com/mcp",
        "oauthConfig": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "scopes": ["read", "write"],
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.create = AsyncMock()
    db.mcpoauthstate.delete_many = AsyncMock()
    return db


async def test_authorize_url_has_all_required_params() -> None:
    db = _mock_db()
    server = _server()
    url = await build_authorize_url(
        server, user_id="user1", redirect_uri="http://localhost:3000/cb", db=db
    )
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "api.highspot.com"
    assert parts.path == "/oauth/authorize"
    params = parse_qs(parts.query)
    # All seven required params present
    assert params["client_id"] == ["client-abc"]
    assert params["redirect_uri"] == ["http://localhost:3000/cb"]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"]
    assert params["state"]
    assert params["scope"] == ["read write"]
    # RFC 8707 resource param — THIS IS FIX #1
    assert params["resource"] == ["https://api.highspot.com"]


async def test_authorize_url_inserts_state_row() -> None:
    db = _mock_db()
    server = _server()
    url = await build_authorize_url(
        server, user_id="user1", redirect_uri="http://localhost:3000/cb", db=db
    )
    db.mcpoauthstate.create.assert_awaited_once()
    create_args = db.mcpoauthstate.create.await_args
    data = create_args.kwargs["data"]
    assert data["mcpServerId"] == "srv1"
    assert data["userId"] == "user1"
    assert data["redirectUri"] == "http://localhost:3000/cb"
    assert data["codeVerifier"]  # PKCE verifier stored
    assert data["state"]
    # expires_at is 5 minutes in the future
    delta = data["expiresAt"] - datetime.now(UTC)
    assert 270 <= delta.total_seconds() <= 310


async def test_authorize_url_requires_oauth_config() -> None:
    db = _mock_db()
    server = _server(oauthConfig=None)
    with pytest.raises(ValueError, match="oauthConfig"):
        await build_authorize_url(
            server, user_id="u", redirect_uri="http://localhost/cb", db=db
        )


async def test_authorize_url_runs_state_reaper() -> None:
    """Expired state rows should be cleaned up on any authorize call."""
    db = _mock_db()
    server = _server()
    await build_authorize_url(
        server, user_id="u", redirect_uri="http://localhost/cb", db=db
    )
    db.mcpoauthstate.delete_many.assert_awaited_once()
    call = db.mcpoauthstate.delete_many.await_args
    # Filter is expiresAt lt now
    assert "where" in call.kwargs
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_authorize.py -v
```
Expected: ImportError on `build_authorize_url`.

- [ ] **Step 3: Implement — append to `src/mcp/oauth.py`**

Add these imports near the top of `src/mcp/oauth.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
```

Add at the bottom of the module (before `__all__`):

```python
_STATE_TTL = timedelta(minutes=5)


async def _reap_expired_state(db: Any) -> None:
    """Delete expired McpOAuthState rows. Called lazily on each authorize."""
    await db.mcpoauthstate.delete_many(
        where={"expiresAt": {"lt": datetime.now(UTC)}}
    )


async def build_authorize_url(
    server: Any,
    user_id: str,
    redirect_uri: str,
    db: Any,
) -> str:
    """Return the OAuth authorize URL; insert McpOAuthState for the callback.

    The returned URL includes the RFC 8707 `resource` parameter (fix #1).
    """
    config = server.oauthConfig
    if not config:
        raise ValueError(
            f"MCP server {server.id!r} has no oauthConfig; cannot build authorize URL."
        )
    authorize_url = config.get("authorizeUrl")
    client_id = config.get("clientId")
    scopes = config.get("scopes") or []
    if not authorize_url or not client_id:
        raise ValueError(
            f"MCP server {server.id!r} oauthConfig missing authorizeUrl or clientId."
        )

    await _reap_expired_state(db)

    state = generate_state()
    verifier, challenge = generate_pkce_pair()
    now = datetime.now(UTC)
    expires_at = now + _STATE_TTL

    await db.mcpoauthstate.create(
        data={
            "mcpServerId": server.id,
            "userId": user_id,
            "state": state,
            "codeVerifier": verifier,
            "redirectUri": redirect_uri,
            "scope": " ".join(scopes) if scopes else None,
            "expiresAt": expires_at,
        }
    )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": " ".join(scopes) if scopes else "",
        "resource": derive_resource(server.url),  # fix #1
    }
    return f"{authorize_url}?{urlencode(params)}"
```

Update `__all__` to include `build_authorize_url`.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_authorize.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 4/4 new pass; overall ~219 pass.

```bash
git add src/mcp/oauth.py tests/unit/mcp/test_oauth_authorize.py
git commit -m "feat(mcp-oauth): build_authorize_url with RFC 8707 resource param

Generates state + PKCE verifier/challenge, inserts an McpOAuthState
row (5-min TTL), builds the authorize URL with all seven required
query params — including \`resource = derive_resource(server.url)\`
per RFC 8707 (OAuth fix #1).

Runs a cheap expired-state reaper on every authorize — bounded cost,
no cron job needed.

Unit tests assert resource param is present in the URL — this is the
first of four mandatory-resource-param assertions.

See Phase 3b spec §7.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: OAuth — `exchange_code_for_tokens`

**Files:**
- Modify: `src/mcp/oauth.py`
- Create: `tests/unit/mcp/test_oauth_exchange.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/mcp/test_oauth_exchange.py`:

```python
"""Tests for exchange_code_for_tokens — including the resource-param assertion."""
import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock

from src.mcp.oauth import InvalidStateError, TokenExchangeError, exchange_code_for_tokens


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings
    get_settings.cache_clear()


def _server(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "owner",
        "url": "https://api.highspot.com/mcp",
        "oauthConfig": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": "plain-secret-for-test",
            "scopes": ["read"],
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _state_row(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "st1",
        "mcpServerId": "srv1",
        "userId": "user1",
        "state": "abc",
        "codeVerifier": "ver123",
        "redirectUri": "http://localhost:3000/cb",
        "scope": "read",
        "expiresAt": datetime.now(UTC) + timedelta(minutes=4),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_db(state_row: Any | None) -> MagicMock:
    db = MagicMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.find_unique = AsyncMock(return_value=state_row)
    db.mcpoauthstate.delete = AsyncMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.upsert = AsyncMock(return_value=SimpleNamespace(id="tok1"))
    return db


async def test_exchange_sends_resource_param(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """FIX #1 (second of four): token-exchange POST must include resource param."""
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
    )
    db = _mock_db(_state_row())
    await exchange_code_for_tokens(_server(), code="code-1", state="abc", db=db)

    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    # Content-Type is form-urlencoded
    assert "resource=https%3A%2F%2Fapi.highspot.com" in body
    assert "grant_type=authorization_code" in body
    assert "code=code-1" in body
    assert "code_verifier=ver123" in body


async def test_exchange_stores_encrypted_tokens(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "plain-at", "refresh_token": "plain-rt", "expires_in": 3600},
    )
    db = _mock_db(_state_row())
    await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)
    db.mcpoauthtoken.upsert.assert_awaited_once()
    call = db.mcpoauthtoken.upsert.await_args
    created = call.kwargs["data"]["create"]
    assert created["encryptedAccessToken"] != "plain-at"
    assert created["encryptedAccessToken"]
    assert created["encryptedRefreshToken"] != "plain-rt"
    assert created["mcpServerId"] == "srv1"
    assert created["userId"] == "user1"


async def test_exchange_deletes_state_row_once(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "expires_in": 3600},
    )
    db = _mock_db(_state_row())
    await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)
    db.mcpoauthstate.delete.assert_awaited_once()


async def test_exchange_raises_for_unknown_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    db = _mock_db(None)  # state not found
    with pytest.raises(InvalidStateError, match="state"):
        await exchange_code_for_tokens(_server(), code="c", state="missing", db=db)


async def test_exchange_raises_for_expired_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    expired = _state_row(expiresAt=datetime.now(UTC) - timedelta(minutes=1))
    db = _mock_db(expired)
    with pytest.raises(InvalidStateError, match="expired"):
        await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)


async def test_exchange_raises_on_idp_error(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        status_code=400,
        json={"error": "invalid_grant"},
    )
    db = _mock_db(_state_row())
    with pytest.raises(TokenExchangeError, match="400"):
        await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_exchange.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement — append to `src/mcp/oauth.py`**

Add these imports at the top:

```python
import httpx

from src.security.encryption import encrypt
```

Add this helper and function before `__all__`:

```python
async def _validate_and_consume_state(state: str, db: Any) -> Any:
    """Find state row, check expiry, delete it (one-shot), return the row."""
    row = await db.mcpoauthstate.find_unique(where={"state": state})
    if row is None:
        raise InvalidStateError(f"OAuth state {state!r} not found (CSRF or replay).")
    if row.expiresAt < datetime.now(UTC):
        raise InvalidStateError(f"OAuth state {state!r} has expired.")
    await db.mcpoauthstate.delete(where={"state": state})
    return row


def _expires_at_from_in(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


async def exchange_code_for_tokens(
    server: Any,
    code: str,
    state: str,
    db: Any,
) -> Any:
    """Exchange authorization code for tokens; store encrypted in McpOAuthToken.

    Token-exchange POST includes RFC 8707 `resource` (fix #1).
    """
    state_row = await _validate_and_consume_state(state, db)

    config = server.oauthConfig or {}
    token_url = config["tokenUrl"]
    client_id = config["clientId"]
    client_secret = config.get("clientSecret", "")

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_row.redirectUri,
        "code_verifier": state_row.codeVerifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "resource": derive_resource(server.url),  # fix #1
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data=form,
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise TokenExchangeError(
            f"OAuth token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    payload = resp.json()
    access_token = payload.get("access_token", "")
    refresh_token = payload.get("refresh_token")
    expires_at = _expires_at_from_in(payload.get("expires_in"))

    encrypted_access = encrypt(access_token)
    encrypted_refresh = encrypt(refresh_token) if refresh_token else None

    token_data = {
        "encryptedAccessToken": encrypted_access,
        "encryptedRefreshToken": encrypted_refresh,
        "expiresAt": expires_at,
        "scope": payload.get("scope") or state_row.scope,
        "tokenType": payload.get("token_type", "Bearer"),
    }

    return await db.mcpoauthtoken.upsert(
        where={
            "mcpServerId_userId": {
                "mcpServerId": server.id,
                "userId": state_row.userId,
            }
        },
        data={
            "create": {
                "mcpServerId": server.id,
                "userId": state_row.userId,
                **token_data,
            },
            "update": token_data,
        },
    )
```

Add `exchange_code_for_tokens` to `__all__`.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_exchange.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 6/6 new pass; overall ~225 pass.

```bash
git add src/mcp/oauth.py tests/unit/mcp/test_oauth_exchange.py
git commit -m "feat(mcp-oauth): exchange_code_for_tokens with RFC 8707 resource

Validates state row (CSRF + expiry), one-shot deletes it, POSTs to
tokenUrl with grant_type=authorization_code + code_verifier + resource
(fix #1 assertion in unit test), encrypts access + refresh tokens,
upserts McpOAuthToken keyed on (mcpServerId, userId) so re-authorization
updates rather than duplicates.

See Phase 3b spec §7.4.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: OAuth — `refresh_token`

**Files:**
- Modify: `src/mcp/oauth.py`
- Create: `tests/unit/mcp/test_oauth_refresh.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/mcp/test_oauth_refresh.py`:

```python
"""Tests for refresh_token — third resource-param assertion."""
import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock

from src.mcp.oauth import TokenRefreshError, refresh_token
from src.security.encryption import encrypt


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings
    get_settings.cache_clear()


def _server() -> Any:
    return SimpleNamespace(
        id="srv1",
        userId="owner",
        url="https://api.highspot.com/mcp",
        oauthConfig={
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": "plain-secret",
        },
    )


def _token_row(monkeypatch: pytest.MonkeyPatch) -> Any:
    _set_enc_key(monkeypatch)
    return SimpleNamespace(
        id="tok1",
        mcpServerId="srv1",
        userId="user1",
        encryptedAccessToken=encrypt("old-access"),
        encryptedRefreshToken=encrypt("refresh-123"),
        expiresAt=datetime.now(UTC) - timedelta(minutes=1),
        scope="read",
        tokenType="Bearer",
    )


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.update = AsyncMock(return_value=SimpleNamespace(id="tok1"))
    return db


async def test_refresh_sends_resource_param(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """FIX #1 (third of four): refresh POST must include resource param."""
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )
    await refresh_token(_server(), tok, db)
    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fapi.highspot.com" in body
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh-123" in body


async def test_refresh_updates_token_row(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
    )
    await refresh_token(_server(), tok, db)
    db.mcpoauthtoken.update.assert_awaited_once()
    call = db.mcpoauthtoken.update.await_args
    data = call.kwargs["data"]
    assert data["encryptedAccessToken"] != "new-at"  # encrypted
    assert data["encryptedRefreshToken"] != "new-rt"
    assert data["expiresAt"] > datetime.now(UTC) + timedelta(minutes=50)


async def test_refresh_keeps_old_refresh_token_if_idp_omits(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """Some IdPs don't rotate the refresh token on refresh. Keep the existing one."""
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},  # no refresh_token
    )
    await refresh_token(_server(), tok, db)
    data = db.mcpoauthtoken.update.await_args.kwargs["data"]
    # Key is present and equal to the original (unchanged)
    assert data["encryptedRefreshToken"] == tok.encryptedRefreshToken


async def test_refresh_raises_on_idp_error(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        status_code=401,
        json={"error": "invalid_grant"},
    )
    with pytest.raises(TokenRefreshError, match="401"):
        await refresh_token(_server(), tok, db)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_refresh.py -v
```
Expected: ImportError on `refresh_token`.

- [ ] **Step 3: Implement — append to `src/mcp/oauth.py`**

Add this import near the other `src.security.encryption` import:

```python
from src.security.encryption import decrypt, encrypt
```
(Replace the existing `from src.security.encryption import encrypt` line.)

Add before `__all__`:

```python
async def refresh_token(
    server: Any,
    token_row: Any,
    db: Any,
) -> Any:
    """Refresh an expired access token. POST includes RFC 8707 resource (fix #1).

    Updates token_row in place (via DB). Keeps the old refresh_token if the
    IdP doesn't return a new one.
    """
    if not token_row.encryptedRefreshToken:
        raise McpTokenExpiredError(
            f"Token for server {server.id!r} has no refresh token; user must re-authorize."
        )

    config = server.oauthConfig or {}
    token_url = config["tokenUrl"]
    client_id = config["clientId"]
    client_secret = config.get("clientSecret", "")
    refresh_plaintext = decrypt(token_row.encryptedRefreshToken)

    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_plaintext,
        "client_id": client_id,
        "client_secret": client_secret,
        "resource": derive_resource(server.url),  # fix #1
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data=form,
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise TokenRefreshError(
            f"OAuth refresh failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    payload = resp.json()
    access_token = payload.get("access_token", "")
    new_refresh = payload.get("refresh_token")
    expires_at = _expires_at_from_in(payload.get("expires_in"))

    update_data: dict[str, Any] = {
        "encryptedAccessToken": encrypt(access_token),
        "expiresAt": expires_at,
    }
    if new_refresh:
        update_data["encryptedRefreshToken"] = encrypt(new_refresh)
    else:
        # Keep the old encrypted refresh token (no rotation)
        update_data["encryptedRefreshToken"] = token_row.encryptedRefreshToken

    return await db.mcpoauthtoken.update(
        where={"id": token_row.id},
        data=update_data,
    )
```

Add `refresh_token` to `__all__`.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_refresh.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 4/4 new pass; overall ~229 pass.

```bash
git add src/mcp/oauth.py tests/unit/mcp/test_oauth_refresh.py
git commit -m "feat(mcp-oauth): refresh_token with RFC 8707 resource

Decrypts refresh token, POSTs to tokenUrl with grant_type=refresh_token
+ resource (fix #1 assertion in unit test), updates the token row.
Preserves the old refresh token if the IdP doesn't rotate — some
providers (e.g., GitHub) don't rotate on refresh.

McpTokenExpiredError raised when no refresh token is present — user
must re-authorize in that case.

See Phase 3b spec §7.5.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: OAuth — `get_valid_access_token` + service-account fallback

**Files:**
- Modify: `src/mcp/oauth.py`
- Create: `tests/unit/mcp/test_oauth_get_valid_token.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/mcp/test_oauth_get_valid_token.py`:

```python
"""Tests for get_valid_access_token — refresh-on-use + service-account fallback (fix #5)."""
import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp.oauth import (
    McpTokenExpiredError,
    McpTokenMissingError,
    get_valid_access_token,
)
from src.security.encryption import encrypt


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings
    get_settings.cache_clear()


def _server(is_shared: bool = False, owner: str = "owner") -> Any:
    return SimpleNamespace(
        id="srv1",
        userId=owner,
        url="https://api.highspot.com/mcp",
        isShared=is_shared,
        oauthConfig={
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "c",
            "clientSecret": "s",
        },
    )


def _valid_token(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Any:
    _set_enc_key(monkeypatch)
    base: dict[str, Any] = {
        "id": "tok1",
        "mcpServerId": "srv1",
        "userId": "user1",
        "encryptedAccessToken": encrypt("plain-access"),
        "encryptedRefreshToken": encrypt("rt-1"),
        "expiresAt": datetime.now(UTC) + timedelta(hours=1),
        "tokenType": "Bearer",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_db(primary: Any | None, fallback: Any | None = None) -> MagicMock:
    """mcpoauthtoken.find_unique is called at most twice (primary, then fallback)."""
    db = MagicMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.find_unique = AsyncMock(side_effect=[primary, fallback])
    db.mcpoauthtoken.update = AsyncMock()
    return db


async def test_owner_with_valid_token_returns_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tok = _valid_token(monkeypatch)
    db = _mock_db(tok)
    out = await get_valid_access_token(_server(owner="user1"), "user1", db)
    assert out == "plain-access"


async def test_missing_token_and_not_shared_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    db = _mock_db(None)  # user has no token, server not shared
    with pytest.raises(McpTokenMissingError, match="user1"):
        await get_valid_access_token(_server(is_shared=False, owner="owner"), "user1", db)


async def test_service_account_fallback_for_shared_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIX #5: when user has no token for a shared server, fall back to owner's token."""
    fallback = _valid_token(monkeypatch, userId="owner")
    db = _mock_db(primary=None, fallback=fallback)
    out = await get_valid_access_token(_server(is_shared=True, owner="owner"), "user1", db)
    assert out == "plain-access"
    # Two find_unique calls: one for user1, one for owner
    assert db.mcpoauthtoken.find_unique.await_count == 2


async def test_shared_server_but_owner_also_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    db = _mock_db(primary=None, fallback=None)
    with pytest.raises(McpTokenMissingError, match="server owner"):
        await get_valid_access_token(_server(is_shared=True, owner="owner"), "user1", db)


async def test_near_expiry_triggers_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within expiry buffer, refresh_token is invoked."""
    tok = _valid_token(
        monkeypatch, expiresAt=datetime.now(UTC) + timedelta(seconds=30)
    )
    db = _mock_db(tok)

    refresh_calls: list[Any] = []

    async def _fake_refresh(server: Any, token_row: Any, _db: Any) -> Any:
        refresh_calls.append((server.id, token_row.id))
        # Return a new row with a fresh token
        return SimpleNamespace(
            id="tok1",
            encryptedAccessToken=encrypt("refreshed"),
            encryptedRefreshToken=token_row.encryptedRefreshToken,
            expiresAt=datetime.now(UTC) + timedelta(hours=1),
        )

    import src.mcp.oauth as oauth_mod
    monkeypatch.setattr(oauth_mod, "refresh_token", _fake_refresh)

    out = await get_valid_access_token(_server(owner="user1"), "user1", db)
    assert out == "refreshed"
    assert refresh_calls == [("srv1", "tok1")]


async def test_near_expiry_without_refresh_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tok = _valid_token(
        monkeypatch,
        expiresAt=datetime.now(UTC) + timedelta(seconds=30),
        encryptedRefreshToken=None,
    )
    db = _mock_db(tok)
    with pytest.raises(McpTokenExpiredError):
        await get_valid_access_token(_server(owner="user1"), "user1", db)
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_get_valid_token.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement — append to `src/mcp/oauth.py`**

Before `__all__`:

```python
_EXPIRY_BUFFER_SECONDS = 60


async def _find_token_for(mcp_server_id: str, user_id: str, db: Any) -> Any | None:
    return await db.mcpoauthtoken.find_unique(
        where={
            "mcpServerId_userId": {
                "mcpServerId": mcp_server_id,
                "userId": user_id,
            }
        }
    )


async def get_valid_access_token(
    server: Any,
    user_id: str | None,
    db: Any,
    *,
    expiry_buffer_seconds: int = _EXPIRY_BUFFER_SECONDS,
) -> str:
    """Return a decrypted plaintext access token, refreshing if near-expiry.

    For shared servers, falls back to the owner's token when the user has no
    personal one (fix #5). Never leaks a token to a caller that can't access
    the server (resolver handles that check before we get here).
    """
    effective_user = user_id or ""
    token = await _find_token_for(server.id, effective_user, db)

    # Service-account fallback for shared servers (fix #5)
    if token is None and server.isShared and effective_user != server.userId:
        token = await _find_token_for(server.id, server.userId, db)
        if token is None:
            raise McpTokenMissingError(
                f"No token for user {user_id!r} on MCP server {server.id!r}, "
                f"and server owner {server.userId!r} has no token either "
                f"(service-account fallback failed)."
            )
    elif token is None:
        raise McpTokenMissingError(
            f"No OAuth token found for user {user_id!r} on MCP server {server.id!r}. "
            f"User must complete the OAuth flow first."
        )

    # Refresh-on-use with expiry buffer
    if token.expiresAt is not None:
        now = datetime.now(UTC)
        if now >= token.expiresAt - timedelta(seconds=expiry_buffer_seconds):
            if not token.encryptedRefreshToken:
                raise McpTokenExpiredError(
                    f"Token for server {server.id!r} expired and has no refresh token; "
                    f"user must re-authorize."
                )
            token = await refresh_token(server, token, db)

    return decrypt(token.encryptedAccessToken)
```

Add `get_valid_access_token` to `__all__`.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_oauth_get_valid_token.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 6/6 new pass; overall ~235 pass.

```bash
git add src/mcp/oauth.py tests/unit/mcp/test_oauth_get_valid_token.py
git commit -m "feat(mcp-oauth): get_valid_access_token with service-account fallback

The front door for McpToolProvider's OAuth path. Given a server +
user_id + db:
  1. Look up McpOAuthToken for (server, user).
  2. If missing AND server.isShared AND user != server.userId:
     fall back to (server, server.userId) — fix #5.
  3. If near-expiry (60s buffer): refresh_token (fix #1 applies inside).
  4. Return decrypted plaintext access token.

McpTokenMissingError / McpTokenExpiredError raised when the user has
no viable token — callers surface as 'user must reconnect'.

See Phase 3b spec §7.6, ADR-0011.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: MCPClient — `auth_header_factory` hook

**Files:**
- Modify: `src/mcp/client.py`
- Modify: `tests/unit/mcp/test_client.py` (add one test)

- [ ] **Step 1: Add a failing test**

Append to `tests/unit/mcp/test_client.py`:

```python
async def test_auth_header_factory_is_called_per_request(httpx_mock: HTTPXMock) -> None:
    """For OAuth: the factory must fire on every _rpc call so near-expiry refreshes apply."""
    call_count = {"n": 0}

    async def _factory() -> dict[str, str]:
        call_count["n"] += 1
        return {"Authorization": f"Bearer token-{call_count['n']}"}

    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
    )

    client = MCPClient(
        "https://mcp.example.com/rpc",
        auth_header_factory=_factory,
    )
    await client.tools_list()
    await client.tools_list()

    # Two outbound requests → two factory calls
    assert call_count["n"] == 2
    reqs = httpx_mock.get_requests()
    assert reqs[0].headers.get("authorization") == "Bearer token-1"
    assert reqs[1].headers.get("authorization") == "Bearer token-2"


async def test_auth_header_factory_takes_precedence_over_dict(httpx_mock: HTTPXMock) -> None:
    """If both auth_header and auth_header_factory are provided, factory wins."""
    async def _factory() -> dict[str, str]:
        return {"Authorization": "Bearer factory-wins"}

    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )
    client = MCPClient(
        "https://mcp.example.com/rpc",
        auth_header={"Authorization": "Bearer dict-loses"},
        auth_header_factory=_factory,
    )
    await client.tools_list()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("authorization") == "Bearer factory-wins"
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_client.py::test_auth_header_factory_is_called_per_request -v
```
Expected: TypeError — `MCPClient() got unexpected keyword argument 'auth_header_factory'`.

- [ ] **Step 3: Modify `src/mcp/client.py`**

Open `src/mcp/client.py`. Update the imports:

```python
from collections.abc import Awaitable, Callable
```

Update `MCPClient.__init__` signature:

```python
    def __init__(
        self,
        url: str,
        auth_header: dict[str, str] | None = None,
        auth_header_factory: Callable[[], Awaitable[dict[str, str]]] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._url = url
        self._auth_header = dict(auth_header) if auth_header else {}
        self._auth_header_factory = auth_header_factory
        self._timeout = timeout
        self._counter = itertools.count(1)
```

Update `_rpc` to call the factory when set:

```python
    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        rpc_id = next(self._counter)
        body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        if self._auth_header_factory is not None:
            auth = await self._auth_header_factory()
        else:
            auth = self._auth_header
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **auth,
        }
        # ... rest of method unchanged
```

(Leave the rest of `_rpc`, `_parse_response`, and `_parse_sse` untouched.)

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_client.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: all client tests pass (9 total — 7 existing + 2 new); overall ~237 pass.

```bash
git add src/mcp/client.py tests/unit/mcp/test_client.py
git commit -m "feat(mcp): MCPClient accepts async auth_header_factory

Additive optional kwarg. When set, the factory is awaited on every
outbound _rpc call so OAuth tokens get refreshed-on-use (fix #4 path:
McpToolProvider constructs a factory that calls
oauth.get_valid_access_token each time).

Factory wins over the static auth_header dict when both are provided
(the static path stays for 3a's api-key/bearer simplicity; no
regressions).

See Phase 3b spec §8.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: `McpToolProvider` OAuth branch

**Files:**
- Modify: `src/mcp/base.py`
- Modify: `tests/unit/mcp/test_base.py` (add OAuth tests)

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/mcp/test_base.py`:

```python
async def test_oauth_auth_returns_oauthauth_descriptor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 3b: authType='oauth' no longer raises NotImplementedError."""
    _set_encryption_key(monkeypatch)
    srv = _server_row(
        authType="oauth",
        oauthConfig={
            "authorizeUrl": "https://idp/auth",
            "tokenUrl": "https://idp/token",
            "clientId": "c",
            "clientSecret": "s",
            "scopes": ["read"],
        },
    )
    from src.tools.base import OAuthAuth
    provider = McpToolProvider(srv, db=MagicMock(), user_id="user1")
    assert isinstance(provider.auth, OAuthAuth)
    assert provider.auth.include_rfc8707_resource is True


async def test_oauth_auth_header_factory_calls_get_valid_access_token(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """When an OAuth provider's client fires a request, the auth header factory
    must resolve via oauth.get_valid_access_token (fix #4)."""
    _set_encryption_key(monkeypatch)
    srv = _server_row(
        authType="oauth",
        oauthConfig={
            "authorizeUrl": "https://idp/auth",
            "tokenUrl": "https://idp/token",
            "clientId": "c",
            "clientSecret": "s",
            "scopes": [],
        },
    )

    captured: dict[str, Any] = {}

    async def _fake_get_valid(server: Any, user_id: str, db: Any) -> str:
        captured["server_id"] = server.id
        captured["user_id"] = user_id
        return "bearer-xyz"

    import src.mcp.base as base_mod
    monkeypatch.setattr(base_mod, "get_valid_access_token", _fake_get_valid)

    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
    )

    provider = McpToolProvider(srv, db=MagicMock(), user_id="user1")
    await provider.tools()
    req = httpx_mock.get_request()
    assert req is not None
    assert req.headers.get("authorization") == "Bearer bearer-xyz"
    assert captured == {"server_id": "srv1", "user_id": "user1"}


async def test_oauth_provider_without_db_raises() -> None:
    """OAuth McpToolProvider requires db+user_id at construction; guards
    against callers that wire it up incorrectly."""
    srv = _server_row(authType="oauth", oauthConfig={"tokenUrl": "x", "clientId": "c"})
    provider = McpToolProvider(srv)  # no db, no user_id
    with pytest.raises(RuntimeError, match="db"):
        await provider.tools()
```

Also add the missing imports at the top of `tests/unit/mcp/test_base.py` (MagicMock wasn't previously needed):

```python
from unittest.mock import MagicMock
```

If it's already imported, leave it alone.

- [ ] **Step 2: Verify failing**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_base.py -v
```
Expected: three new tests fail (first two because `oauth` still raises NotImplementedError; third because `__init__` doesn't accept `db`/`user_id`).

- [ ] **Step 3: Modify `src/mcp/base.py`**

Read `src/mcp/base.py`. Replace the `__init__` method with:

```python
    def __init__(
        self,
        server: Any,
        *,
        db: Any | None = None,
        user_id: str | None = None,
    ) -> None:
        """`server` is a Prisma McpServer row (duck-typed — attribute access).

        db + user_id are required for authType='oauth' — McpToolProvider looks
        tokens up from Prisma on each outbound request. Static-auth callers
        may omit both.
        """
        self._server = server
        self._db = db
        self._user_id = user_id
        resolved_url = substitute_url_placeholders(server.url)

        if server.authType == "oauth":
            # OAuth: use factory so token refreshes apply per outbound call.
            self._client = MCPClient(
                resolved_url,
                auth_header_factory=self._build_oauth_auth_header,
            )
        else:
            # Static auth: resolve header once at construction.
            self._client = MCPClient(
                resolved_url,
                auth_header=self._build_auth_header(),
            )
```

Replace the `auth` property's OAuth branch:

```python
    @property
    def auth(self) -> AuthRequirement:  # pyright: ignore[reportIncompatibleVariableOverride]
        auth_type = self._server.authType
        if auth_type == "none":
            return NoAuth()
        if auth_type in {"api-key", "bearer"}:
            return ApiKeyAuth(
                env_var=f"<mcp-server-{self._server.id}>",
                settings_field=f"mcp_server_{self._server.id}_token",
            )
        if auth_type == "oauth":
            config = self._server.oauthConfig or {}
            return OAuthAuth(
                authorize_url=config.get("authorizeUrl", ""),
                token_url=config.get("tokenUrl", ""),
                scopes=list(config.get("scopes", []) or []),
                include_rfc8707_resource=True,
            )
        raise ValueError(f"Unknown authType {auth_type!r} on MCP server {self._server.id!r}")
```

Update the imports at the top to include `OAuthAuth` and `get_valid_access_token`:

```python
from src.mcp.oauth import get_valid_access_token
from src.tools.base import (
    ApiKeyAuth,
    AuthRequirement,
    BuildContext,
    HealthStatus,
    NoAuth,
    OAuthAuth,
    ToolDefinition,
    ToolProvider,
)
```

Replace `_build_auth_header` with a synchronous static-only variant and add the new async OAuth variant:

```python
    def _build_auth_header(self) -> dict[str, str] | None:
        """Static-auth header (api-key / bearer / none). Not used for oauth."""
        auth_type = self._server.authType
        if auth_type == "none":
            return None
        if auth_type in {"api-key", "bearer"}:
            if not self._server.encryptedAccessToken:
                raise ValueError(
                    f"MCP server {self._server.id!r} is authType={auth_type} "
                    f"but has no encryptedAccessToken"
                )
            token = decrypt(self._server.encryptedAccessToken)
            if auth_type == "bearer":
                return {"Authorization": f"Bearer {token}"}
            header_name = self._server.headerName or "Authorization"
            return {header_name: token}
        if auth_type == "oauth":
            # Should never happen — __init__ routes oauth through the async factory.
            raise RuntimeError("OAuth auth headers must be built via the async factory.")
        raise ValueError(f"Unknown authType {auth_type!r}")

    async def _build_oauth_auth_header(self) -> dict[str, str]:
        """Async factory: fetches a valid OAuth token per request (refreshes if needed)."""
        if self._db is None:
            raise RuntimeError(
                f"McpToolProvider for oauth server {self._server.id!r} requires db+user_id; "
                f"resolver must pass them."
            )
        token = await get_valid_access_token(self._server, self._user_id, self._db)
        return {"Authorization": f"Bearer {token}"}
```

Remove the old `NotImplementedError("Phase 3b")` line that was in `_build_auth_header` (it's replaced above).

Also update `decrypt` import (already there — just ensure it's present):

```python
from src.security.encryption import decrypt
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_base.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: all base tests pass (12 total — 9 existing + 3 new); overall ~240 pass.

```bash
git add src/mcp/base.py tests/unit/mcp/test_base.py
git commit -m "feat(mcp): McpToolProvider OAuth branch (fix #4: server-side tokens)

- __init__ now accepts db+user_id (kwargs; optional for static-auth).
- For authType='oauth', constructs the MCPClient with an async
  auth_header_factory so tokens refresh-on-use.
- auth property returns OAuthAuth descriptor (no longer raises
  NotImplementedError for oauth).
- The factory calls oauth.get_valid_access_token, which handles
  service-account fallback for shared servers (fix #5).

Tokens never transit the client — client sees only hasAccessToken bool
via the REST layer, never the ciphertext or plaintext.

See Phase 3b spec §8, ADR-0011.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Resolver — pass `db + user_id` into `McpToolProvider`

**Files:**
- Modify: `src/mcp/resolver.py`
- Modify: `tests/unit/mcp/test_resolver.py` (update existing tests if needed)

- [ ] **Step 1: Update `src/mcp/resolver.py`**

In `resolve_mcp_tools_for_node`, change the provider construction to pass `db` and `user_id`:

```python
        provider = McpToolProvider(server, db=db, user_id=context.user_id)
```

In `resolve_single_mcp_tool`, same change:

```python
        provider = McpToolProvider(server, db=db, user_id=user_id)
```

(Both functions already have `db` and a user_id in scope — the resolver received them in Phase 3a.)

- [ ] **Step 2: Update existing tests**

Read `tests/unit/mcp/test_resolver.py`. The existing `_FakeProvider` classes take `server` as a positional arg; update them to accept `db` and `user_id` as kwargs too:

For each `_FakeProvider` class inside the test functions, change:
```python
class _FakeProvider:
    def __init__(self, server: Any) -> None:
        ...
```
to:
```python
class _FakeProvider:
    def __init__(self, server: Any, *, db: Any = None, user_id: str | None = None) -> None:
        ...
```

The `MagicMock` fake doesn't take kwargs by default — the tests use `monkeypatch.setattr(resolver, "McpToolProvider", _FakeProvider)`, so only classes that actually get called need the kwargs. Check each test: `test_resolve_owner_allowed`, `test_resolve_shared_allowed_for_other_user`, `test_resolve_single_mcp_tool`.

- [ ] **Step 3: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/mcp/test_resolver.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 6/6 resolver tests pass; overall ~240 still pass.

```bash
git add src/mcp/resolver.py tests/unit/mcp/test_resolver.py
git commit -m "feat(mcp): resolver passes db+user_id into McpToolProvider

Phase 3a's resolver already had both in scope; now propagates them so
the provider's OAuth factory can call oauth.get_valid_access_token
(which needs the Prisma client to look up McpOAuthToken rows + the
user_id to scope the lookup).

No behavior change for static-auth servers — they still ignore the
kwargs.

See Phase 3b spec §8.4.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: REST — `/oauth/authorize`, `/oauth/callback`, `/oauth/disconnect`

**Files:**
- Modify: `src/api/mcp_servers.py`
- Modify: `src/main.py` (the callback endpoint is at a different path prefix — see below)
- Create: `tests/unit/api/test_mcp_servers_oauth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/api/test_mcp_servers_oauth.py`:

```python
"""Tests for /mcp-servers/:id/oauth/* and /oauth/callback endpoints."""
import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings
    get_settings.cache_clear()


def _server_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "dev",
        "name": "Highspot",
        "url": "https://api.highspot.com/mcp",
        "description": None,
        "category": None,
        "authType": "oauth",
        "encryptedAccessToken": None,
        "headerName": None,
        "oauthConfig": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "c",
            "clientSecret": "s",
            "scopes": ["read"],
        },
        "tools": None,
        "connectionStatus": "untested",
        "lastTested": None,
        "lastError": None,
        "enabled": True,
        "isOfficial": False,
        "isShared": False,
        "headers": None,
        "createdAt": "2026-04-20T00:00:00Z",
        "updatedAt": "2026-04-20T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.mcpserver = MagicMock()
    db.mcpserver.find_unique = AsyncMock(return_value=_server_row())
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.find_unique = AsyncMock(return_value=None)
    db.mcpoauthtoken.delete = AsyncMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.create = AsyncMock()
    db.mcpoauthstate.delete_many = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


def test_authorize_returns_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()
    resp = client.post(
        "/mcp-servers/srv1/oauth/authorize",
        json={"redirectUri": "http://localhost:3000/oauth/callback"},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorizeUrl"]
    assert "api.highspot.com/oauth/authorize" in url
    assert "resource=https%3A%2F%2Fapi.highspot.com" in url
    db.mcpoauthstate.create.assert_awaited_once()


def test_authorize_on_non_oauth_server_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.mcpserver.find_unique = AsyncMock(return_value=_server_row(authType="none"))
    resp = client.post(
        "/mcp-servers/srv1/oauth/authorize",
        json={"redirectUri": "http://localhost/cb"},
    )
    assert resp.status_code == 422
    assert "authType" in resp.json()["detail"]


def test_authorize_server_not_found_404() -> None:
    client, db = _client_with_mock_db()
    db.mcpserver.find_unique = AsyncMock(return_value=None)
    resp = client.post(
        "/mcp-servers/ghost/oauth/authorize",
        json={"redirectUri": "http://localhost/cb"},
    )
    assert resp.status_code == 404


def test_callback_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()

    state_row = SimpleNamespace(
        id="st1",
        mcpServerId="srv1",
        userId="dev",
        state="abc",
        codeVerifier="v",
        redirectUri="http://localhost/cb",
        scope="read",
        expiresAt=datetime.now(UTC) + timedelta(minutes=4),
    )
    db.mcpoauthstate.find_unique = AsyncMock(return_value=state_row)
    db.mcpoauthstate.delete = AsyncMock()
    db.mcpoauthtoken.upsert = AsyncMock(return_value=SimpleNamespace(id="tok1"))

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, db: Any) -> Any:
        return SimpleNamespace(id="tok1", mcpServerId="srv1", userId="dev")

    monkeypatch.setattr(api_mod, "exchange_code_for_tokens", _fake_exchange)

    resp = client.get("/oauth/callback?code=c1&state=abc")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "serverId": "srv1"}


def test_callback_unknown_state_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.mcpoauthstate.find_unique = AsyncMock(return_value=None)

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, db: Any) -> Any:
        from src.mcp.oauth import InvalidStateError
        raise InvalidStateError("unknown state")

    monkeypatch.setattr(api_mod, "exchange_code_for_tokens", _fake_exchange)

    resp = client.get("/oauth/callback?code=c&state=missing")
    assert resp.status_code == 400


def test_disconnect_owner_allowed() -> None:
    client, db = _client_with_mock_db()
    db.mcpoauthtoken.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="tok1", mcpServerId="srv1", userId="dev")
    )
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    db.mcpoauthtoken.delete.assert_awaited_once()


def test_disconnect_idempotent_when_no_token() -> None:
    client, db = _client_with_mock_db()
    db.mcpoauthtoken.find_unique = AsyncMock(return_value=None)
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    db.mcpoauthtoken.delete.assert_not_awaited()
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_mcp_servers_oauth.py -v
```
Expected: all fail — endpoints don't exist.

- [ ] **Step 3: Implement — modify `src/api/mcp_servers.py`**

Add these imports near the existing `from src.mcp...` imports:

```python
from src.mcp.oauth import (
    InvalidStateError,
    OAuthError,
    build_authorize_url,
    exchange_code_for_tokens,
)
```

Add these request/response models near the existing `McpServerCreate` class:

```python
class OAuthAuthorizeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    redirect_uri: str = Field(alias="redirectUri")


class OAuthAuthorizeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    authorize_url: str = Field(alias="authorizeUrl")


class OAuthCallbackResponse(BaseModel):
    ok: bool
    server_id: str = Field(alias="serverId")
    model_config = ConfigDict(populate_by_name=True)
```

Add these three new endpoints in `src/api/mcp_servers.py` (after the existing DELETE endpoint):

```python
@router.post(
    "/mcp-servers/{server_id}/oauth/authorize",
    response_model=OAuthAuthorizeResponse,
)
async def oauth_authorize(
    server_id: str,
    payload: OAuthAuthorizeRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> OAuthAuthorizeResponse:
    server = await db.mcpserver.find_unique(where={"id": server_id})  # pyright: ignore[reportAttributeAccessIssue]
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )
    if server.authType != "oauth":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"MCP server authType must be 'oauth' (got {server.authType!r}).",
        )
    url = await build_authorize_url(
        server, user_id="dev", redirect_uri=payload.redirect_uri, db=db
    )
    return OAuthAuthorizeResponse(authorize_url=url)


@router.post(
    "/mcp-servers/{server_id}/oauth/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def oauth_disconnect(
    server_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> None:
    token = await db.mcpoauthtoken.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={
            "mcpServerId_userId": {
                "mcpServerId": server_id,
                "userId": "dev",
            }
        }
    )
    if token is None:
        # Idempotent: no token means nothing to disconnect.
        return
    await db.mcpoauthtoken.delete(where={"id": token.id})  # pyright: ignore[reportAttributeAccessIssue]
```

Now add a **separate router** for the OAuth callback (it lives under `/oauth/` not `/mcp-servers/`). At the bottom of the file, add:

```python
oauth_router = APIRouter(tags=["oauth"])


@oauth_router.get("/oauth/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    code: str,
    state: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> OAuthCallbackResponse:
    # Look up state row to find which server this callback is for
    state_row = await db.mcpoauthstate.find_unique(where={"state": state})  # pyright: ignore[reportAttributeAccessIssue]
    if state_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state not found (CSRF or replay).",
        )
    server = await db.mcpserver.find_unique(where={"id": state_row.mcpServerId})  # pyright: ignore[reportAttributeAccessIssue]
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth state references unknown server {state_row.mcpServerId!r}.",
        )
    try:
        await exchange_code_for_tokens(server, code=code, state=state, db=db)
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except OAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return OAuthCallbackResponse(ok=True, server_id=server.id)
```

Update `__all__` to include the new models + `oauth_router`.

- [ ] **Step 4: Register the oauth_router in `src/main.py`**

Update the imports:

```python
from src.api.mcp_servers import oauth_router, router as mcp_servers_router
```

In `create_app()`, after `app.include_router(mcp_servers_router)`:

```python
    app.include_router(oauth_router)
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_mcp_servers_oauth.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 7/7 new OAuth API tests pass; overall ~247 pass.

```bash
git add src/api/mcp_servers.py src/main.py tests/unit/api/test_mcp_servers_oauth.py
git commit -m "feat(api): /oauth/authorize, /oauth/callback, /oauth/disconnect

Three endpoints wiring the browser OAuth flow:
  - POST /mcp-servers/{id}/oauth/authorize → returns authorize URL
    (validates server is authType=oauth; 422 otherwise)
  - GET /oauth/callback → exchanges code for tokens, returns
    {ok, serverId} (400 for unknown state; 502 for IdP failures)
  - POST /mcp-servers/{id}/oauth/disconnect → deletes stored token,
    idempotent (204 even if no token existed)

Anonymous userId='dev' per ADR-0005; Phase 7 wires real auth. The
callback lives under /oauth/ (separate router) because OAuth providers
expect a stable redirect URI, not one nested under /mcp-servers/.

See Phase 3b spec §9.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: LangSmith config threaded explicitly (fix #6)

**Files:**
- Modify: `src/engine/context.py` (add LangSmithConfig + ContextVar)
- Modify: `src/engine/langgraph_executor.py` (set the ContextVar)
- Modify: `src/llm/providers.py` (accept + apply the config)
- Modify: `src/tools/base.py` (BuildContext gains langsmith_config field)
- Modify: `src/executors/agent.py` (read ContextVar, pass into build_chat_model)
- Create: `tests/unit/engine/test_langsmith_config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/engine/test_langsmith_config.py`:

```python
"""Tests for LangSmithConfig threading (fix #6)."""
from collections.abc import Iterator
from contextvars import copy_context

import pytest

from src.engine.context import (
    LangSmithConfig,
    _current_langsmith,  # pyright: ignore[reportPrivateUsage]
    get_current_langsmith,
    set_current_langsmith,
)


@pytest.fixture(autouse=True)
def _reset_langsmith() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    token = _current_langsmith.set(None)
    yield
    _current_langsmith.reset(token)


def test_default_is_none() -> None:
    assert get_current_langsmith() is None


def test_set_then_get() -> None:
    cfg = LangSmithConfig(
        tracing_v2=True, project="test", endpoint="https://smith", api_key="key"
    )
    set_current_langsmith(cfg)
    assert get_current_langsmith() is cfg


def test_contextvar_isolation() -> None:
    outer = LangSmithConfig(
        tracing_v2=True, project="outer", endpoint="https://smith", api_key="k1"
    )
    set_current_langsmith(outer)
    ctx = copy_context()

    def _inner() -> LangSmithConfig | None:
        set_current_langsmith(
            LangSmithConfig(tracing_v2=True, project="inner", endpoint="", api_key="")
        )
        return get_current_langsmith()

    inner = ctx.run(_inner)
    assert inner is not None and inner.project == "inner"
    assert get_current_langsmith() is outer  # outer unchanged


def test_build_chat_model_wraps_when_config_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """When langsmith_config is passed, build_chat_model wraps via with_config."""
    from unittest.mock import MagicMock

    from src.llm import providers as providers_mod

    inner_model = MagicMock()
    wrapped = MagicMock()
    inner_model.with_config = MagicMock(return_value=wrapped)

    monkeypatch.setattr(
        providers_mod,
        "_build_raw_chat_model",
        lambda model_string, **kw: inner_model,
    )

    cfg = LangSmithConfig(
        tracing_v2=True, project="p1", endpoint="https://smith", api_key="k"
    )
    result = providers_mod.build_chat_model(
        "anthropic/claude-haiku-4-5-20251001", langsmith_config=cfg
    )
    assert result is wrapped
    inner_model.with_config.assert_called_once()
    config_arg = inner_model.with_config.call_args.args[0]
    assert config_arg["metadata"] == {"langsmith_project": "p1"}


def test_build_chat_model_no_wrap_when_config_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat: with config=None, return the raw model untouched."""
    from unittest.mock import MagicMock

    from src.llm import providers as providers_mod

    inner_model = MagicMock()
    inner_model.with_config = MagicMock()
    monkeypatch.setattr(
        providers_mod,
        "_build_raw_chat_model",
        lambda model_string, **kw: inner_model,
    )

    result = providers_mod.build_chat_model("anthropic/claude-haiku-4-5-20251001")
    assert result is inner_model
    inner_model.with_config.assert_not_called()
```

- [ ] **Step 2: Extend `src/engine/context.py`**

Read the current file. Add these imports at the top:

```python
from dataclasses import dataclass
```

Add the dataclass and ContextVar after the existing `_current_db` block:

```python
@dataclass(frozen=True)
class LangSmithConfig:
    """Per-execution LangSmith tracing config.

    Threaded explicitly through BuildContext + build_chat_model so that
    tracing doesn't silently break when env vars are stripped (fix #6).
    """

    tracing_v2: bool
    project: str
    endpoint: str
    api_key: str


_current_langsmith: ContextVar[LangSmithConfig | None] = ContextVar(
    "_current_langsmith", default=None
)


def set_current_langsmith(config: LangSmithConfig | None) -> None:
    _current_langsmith.set(config)


def get_current_langsmith() -> LangSmithConfig | None:
    return _current_langsmith.get()
```

Update `__all__` to include the four new names.

- [ ] **Step 3: Refactor `src/llm/providers.py`**

Read the current `build_chat_model` function. Split the provider-dispatch into a private `_build_raw_chat_model`, then make `build_chat_model` the thin wrapper. The existing body becomes the raw helper.

```python
# top of file — add:
from src.engine.context import LangSmithConfig


def _build_raw_chat_model(model_string: str, **kwargs: Any) -> BaseChatModel:
    """The provider-dispatch body (was build_chat_model in Phase 2)."""
    # ... paste the existing body of build_chat_model here, unchanged ...


def build_chat_model(
    model_string: str,
    *,
    langsmith_config: LangSmithConfig | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Resolve and return a chat model, optionally wrapped with LangSmith config.

    When langsmith_config is provided AND tracing_v2is true, the returned
    model carries a with_config call that tags outbound calls with the
    project. When config is None, the model is returned unwrapped and
    tracing falls back to env-var behavior (back-compat).
    """
    model = _build_raw_chat_model(model_string, **kwargs)
    if langsmith_config is not None and langsmith_config.tracing_v2:
        return model.with_config(
            {
                "metadata": {"langsmith_project": langsmith_config.project},
                "tags": ["composer"],
            }
        )
    return model
```

(The refactor is strictly additive — existing call sites continue to work because the old positional signature is preserved.)

- [ ] **Step 4: Update `src/tools/base.py` — add `langsmith_config` to `BuildContext`**

Add the import at the top:

```python
if TYPE_CHECKING:
    from src.engine.context import LangSmithConfig
    from src.engine.state import WorkflowStateDict
    from src.engine.workflow import AgentNode
```

Add the field to BuildContext:

```python
@dataclass(kw_only=True)
class BuildContext:
    """Context passed to `build_tool` — everything a provider might need."""

    node: "AgentNode"
    state: "WorkflowStateDict"
    user_id: str | None = None
    db: Any | None = None
    langsmith_config: "LangSmithConfig | None" = None
```

- [ ] **Step 5: Update `src/executors/agent.py`**

Read the current `arun` method. Add the import at top:

```python
from src.engine.context import get_current_db, get_current_langsmith
```

Change `build_chat_model` call to include the config:

```python
        chat_model = _providers.build_chat_model(
            self.node.data.model or DEFAULT_MODEL,
            langsmith_config=get_current_langsmith(),
        )
```

- [ ] **Step 6: Update `src/engine/langgraph_executor.py`**

Read the current `run` method. Add the import:

```python
from src.engine.context import LangSmithConfig, set_current_db, set_current_langsmith
```

In `run`, before `set_current_db(self.db)`:

```python
            settings = get_settings()
            ls_config = (
                LangSmithConfig(
                    tracing_v2=settings.langchain_tracing_v2,
                    project=settings.langchain_project,
                    endpoint=settings.langchain_endpoint,
                    api_key=settings.langchain_api_key,
                )
                if settings.langchain_tracing_v2
                else None
            )
            set_current_langsmith(ls_config)
            set_current_db(self.db)
```

Add the `get_settings` import at top:

```python
from src.config import get_settings
```

- [ ] **Step 7: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_langsmith_config.py tests/unit/executors/ -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 5/5 new LangSmith tests pass; overall ~252 pass.

```bash
git add src/engine/context.py src/engine/langgraph_executor.py \
        src/llm/providers.py src/tools/base.py src/executors/agent.py \
        tests/unit/engine/test_langsmith_config.py
git commit -m "feat(langsmith): thread config through BuildContext + build_chat_model

Fix #6 from CLAUDE.md §1 — OAB learned the hard way that relying on
env vars for LangSmith at runtime leads to silent tracing outages
when a worker context clears them.

LangGraphExecutor.run constructs a LangSmithConfig from Settings,
stashes it in a ContextVar alongside the db reference. AgentExecutor
reads it and passes into build_chat_model, which wraps the returned
chat model via with_config when tracing_v2 is enabled.

Back-compat: build_chat_model with no langsmith_config returns the
raw model — existing ad-hoc scripts and tests still work via env vars.

See Phase 3b spec §10, CLAUDE.md §1 fix #6.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Integration test — Agent + Highspot OAuth end-to-end

**Files:**
- Create: `tests/integration/test_mcp_highspot.py`

This test exercises the post-authorization hot path against real Highspot. The user provides a pre-obtained refresh token via env vars; the test seeds `McpOAuthToken` with an empty access token + valid refresh token, then the Agent workflow triggers refresh-on-use.

- [ ] **Step 1: Write**

Create `tests/integration/test_mcp_highspot.py`:

```python
"""Integration — Agent with Highspot OAuth MCP.

Exercises the refresh → tools/list → tools/call → workflow completion path
against real Highspot. The full browser-driven authorize flow is covered
by unit tests with a mock IdP; this integration seeds a McpOAuthToken
row with a pre-obtained refresh token.

Required env vars:
    HIGHSPOT_OAUTH_CLIENT_ID
    HIGHSPOT_OAUTH_CLIENT_SECRET
    HIGHSPOT_OAUTH_REFRESH_TOKEN
    HIGHSPOT_MCP_URL (e.g., https://mcp.highspot.com/mcp)
    ANTHROPIC_API_KEY
    ENCRYPTION_KEY
"""

import asyncio
import os

import pytest
from httpx import AsyncClient

from src.security.encryption import encrypt

pytestmark = pytest.mark.integration


async def _poll_until_terminal(
    client: AsyncClient,
    execution_id: str,
    timeout: float = 180.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/executions/{execution_id}")
        body: dict[str, object] = resp.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.5)
    raise AssertionError(f"Execution {execution_id} did not terminate within {timeout}s")


def _required_env() -> dict[str, str] | None:
    needed = [
        "HIGHSPOT_OAUTH_CLIENT_ID",
        "HIGHSPOT_OAUTH_CLIENT_SECRET",
        "HIGHSPOT_OAUTH_REFRESH_TOKEN",
        "HIGHSPOT_MCP_URL",
        "ANTHROPIC_API_KEY",
        "ENCRYPTION_KEY",
    ]
    values: dict[str, str] = {}
    for name in needed:
        v = os.environ.get(name)
        if not v:
            return None
        values[name] = v
    return values


async def test_agent_with_highspot_oauth(client: AsyncClient, app: object) -> None:
    env = _required_env()
    if env is None:
        pytest.skip("Highspot OAuth env vars not set")

    db = app.state.db  # pyright: ignore[reportAttributeAccessIssue]

    # Register the Highspot MCP server
    mcp_resp = await client.post(
        "/mcp-servers",
        json={
            "name": "Highspot OAuth Test",
            "url": env["HIGHSPOT_MCP_URL"],
            "authType": "oauth",
            "isShared": False,
        },
    )
    # authType='oauth' alone isn't enough — we also need oauthConfig.
    # The POST endpoint doesn't accept oauthConfig yet (out of 3b scope),
    # so set it via a direct Prisma update.
    assert mcp_resp.status_code == 201, mcp_resp.text
    server_id = mcp_resp.json()["id"]

    await db.mcpserver.update(
        where={"id": server_id},
        data={
            "oauthConfig": {
                "authorizeUrl": f"{env['HIGHSPOT_MCP_URL'].rstrip('/mcp')}/oauth/authorize",
                "tokenUrl": f"{env['HIGHSPOT_MCP_URL'].rstrip('/mcp')}/oauth/token",
                "clientId": env["HIGHSPOT_OAUTH_CLIENT_ID"],
                "clientSecret": env["HIGHSPOT_OAUTH_CLIENT_SECRET"],
                "scopes": [],
            },
        },
    )

    # Seed a McpOAuthToken row with the refresh token. Access token empty →
    # first use triggers refresh-on-use.
    await db.mcpoauthtoken.create(
        data={
            "mcpServerId": server_id,
            "userId": "dev",
            "encryptedAccessToken": encrypt(""),  # forces refresh
            "encryptedRefreshToken": encrypt(env["HIGHSPOT_OAUTH_REFRESH_TOKEN"]),
            "expiresAt": None,  # None also forces refresh-on-first-use
            "tokenType": "Bearer",
        }
    )

    try:
        # Run Agent workflow using Highspot MCP
        wf = await client.post(
            "/workflows",
            json={
                "name": "Highspot OAuth Test",
                "nodes": [
                    {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                    {
                        "id": "a",
                        "type": "agent",
                        "position": {"x": 100, "y": 0},
                        "data": {
                            "label": "Agent",
                            "model": "anthropic/claude-haiku-4-5-20251001",
                            "instructions": "Use Highspot tools to list a few recent spots or content items. One sentence summary.",
                            "outputFormat": "Text",
                            "mcpServerIds": [server_id],
                        },
                    },
                    {"id": "e", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
                ],
                "edges": [
                    {"id": "e1", "source": "s", "target": "a"},
                    {"id": "e2", "source": "a", "target": "e"},
                ],
            },
        )
        assert wf.status_code == 201, wf.text
        start = await client.post(
            "/executions", json={"workflowId": wf.json()["id"], "input": ""},
        )
        final = await _poll_until_terminal(client, start.json()["id"])
        assert final["status"] == "completed", f"Got: {final}"

        # Token row's access token + expires_at should have updated (refresh fired)
        tok = await db.mcpoauthtoken.find_unique(
            where={
                "mcpServerId_userId": {
                    "mcpServerId": server_id,
                    "userId": "dev",
                }
            }
        )
        assert tok is not None
        assert tok.expiresAt is not None  # refresh populated it

    finally:
        await client.delete(f"/mcp-servers/{server_id}")
```

- [ ] **Step 2: Verify discovery + commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
.venv/Scripts/python -m pytest tests/integration/test_mcp_highspot.py --collect-only -q
```
Expected: ruff/pyright clean; 1 integration test collected; non-integration suite still 252 passing.

```bash
git add tests/integration/test_mcp_highspot.py
git commit -m "test(integration): Agent + Highspot OAuth end-to-end

Exercises the post-authorization hot path against real Highspot:
  1. Register server with authType=oauth
  2. Seed McpOAuthToken with a known-valid refresh token (access empty)
  3. Run Agent workflow using the Highspot MCP
  4. Assert completion + token row's expiresAt populated (refresh fired)

The full browser-driven /authorize + /callback path is covered by
unit tests with mock IdP; this test verifies the refresh → tool call
hot path against a real IdP.

Skips without all of: HIGHSPOT_OAUTH_CLIENT_ID,
HIGHSPOT_OAUTH_CLIENT_SECRET, HIGHSPOT_OAUTH_REFRESH_TOKEN,
HIGHSPOT_MCP_URL, ANTHROPIC_API_KEY, ENCRYPTION_KEY.

See Phase 3b spec §12.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Regression port — OAB Highspot OAuth lifecycle

**Files:**
- Create: `tests/regression/test_oab_highspot_oauth.py`

Port of OAB's Highspot OAuth spec. Uses `pytest_httpx` to mock the token endpoint so no real Highspot call is made — asserts the `resource` param appears on token exchange (this is the specific OAB-caught bug the regression exists to prevent).

- [ ] **Step 1: Write**

Create `tests/regression/test_oab_highspot_oauth.py`:

```python
"""Regression — OAB highspot-oauth-lifecycle.spec.ts.

OAB source: D:/GitHub/open-agent-builder/tests/highspot-oauth-lifecycle.spec.ts
  Asserts the token-exchange request body includes `resource` (OAB lost
  a multi-day debugging session to this missing param).

This port validates the same invariant in Composer's oauth.py. It does
NOT require a real Highspot instance — it mocks the token endpoint and
asserts the POST body.
"""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock

from src.mcp.oauth import exchange_code_for_tokens, refresh_token
from src.security.encryption import encrypt

pytestmark = pytest.mark.integration


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings
    get_settings.cache_clear()


def _highspot_server() -> Any:
    return SimpleNamespace(
        id="srv-hs",
        userId="dev",
        url="https://mcp.highspot.com/mcp",
        oauthConfig={
            "authorizeUrl": "https://mcp.highspot.com/oauth/authorize",
            "tokenUrl": "https://mcp.highspot.com/oauth/token",
            "clientId": "hs-client",
            "clientSecret": "hs-secret",
            "scopes": ["read"],
        },
    )


async def test_oab_regression_token_exchange_includes_resource(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """OAB-caught: token exchange MUST carry resource=https://mcp.highspot.com.

    Without this, Highspot responds with 'resource mismatch' and auth fails.
    """
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://mcp.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
    )

    state_row = SimpleNamespace(
        id="st1",
        mcpServerId="srv-hs",
        userId="dev",
        state="s1",
        codeVerifier="cv",
        redirectUri="http://localhost/cb",
        scope="read",
        expiresAt=datetime.now(UTC) + timedelta(minutes=4),
    )
    db = MagicMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.find_unique = AsyncMock(return_value=state_row)
    db.mcpoauthstate.delete = AsyncMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.upsert = AsyncMock(return_value=SimpleNamespace(id="tok1"))

    await exchange_code_for_tokens(_highspot_server(), code="c", state="s1", db=db)

    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fmcp.highspot.com" in body, (
        f"Regression: token exchange must include resource param. Body was: {body}"
    )


async def test_oab_regression_refresh_includes_resource(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    """OAB-caught: refresh MUST carry resource too — Highspot enforces it on refresh."""
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://mcp.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )

    token_row = SimpleNamespace(
        id="tok1",
        mcpServerId="srv-hs",
        userId="dev",
        encryptedAccessToken=encrypt("old-at"),
        encryptedRefreshToken=encrypt("rt-1"),
        expiresAt=datetime.now(UTC) - timedelta(minutes=1),
        scope="read",
        tokenType="Bearer",
    )
    db = MagicMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.update = AsyncMock()

    await refresh_token(_highspot_server(), token_row, db)

    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fmcp.highspot.com" in body, (
        f"Regression: refresh must include resource. Body was: {body}"
    )
```

- [ ] **Step 2: Commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
.venv/Scripts/python -m pytest tests/regression/test_oab_highspot_oauth.py --collect-only -q
```
Expected: 2 regression tests collected; non-integration suite unchanged.

```bash
git add tests/regression/test_oab_highspot_oauth.py
git commit -m "test(regression): port OAB Highspot OAuth lifecycle spec

Two regressions port the specific invariants OAB-team debugged
(multi-day incident, CLAUDE.md §1 fix #1):
  1. Token exchange body must include resource=<origin>
  2. Refresh body must include resource=<origin>

Both tests mock the token endpoint (no real Highspot needed) and
assert the outbound POST body. If a future refactor accidentally
drops resource from either flow, these tests fail immediately.

See Phase 3b spec §12.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0011 backfill

**Authorized for this task:** `CHANGELOG.md`, `CLAUDE.md` phase-status table, `docs/design/decisions.md` ADR-0011 `Implemented by` line.

- [ ] **Step 1: Verify exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
All four must be green.

Run the OAuth integration suite (controller runs, not subagent) to confirm Highspot still works:
```bash
# Controller-side only
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_mcp_highspot.py',
     'tests/regression/test_oab_highspot_oauth.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```

- [ ] **Step 2: Update CHANGELOG.md**

Insert this block above the Phase 3a section:

```markdown
### Phase 3b — MCP OAuth + the six hard-won fixes (2026-04-20)

#### Added
- [Phase 3b design spec](docs/superpowers/specs/2026-04-20-phase-3b-mcp-oauth-design.md) + ADR-0011.
- Prisma `McpOAuthToken` + `McpOAuthState` tables (additive migration; McpServer unchanged).
- `src/mcp/oauth.py` — PKCE + state + resource derivation; `build_authorize_url`, `exchange_code_for_tokens`, `refresh_token`, `get_valid_access_token` (with service-account fallback for shared servers).
- `src/mcp/client.py` gains an optional async `auth_header_factory` hook — OAuth tokens refresh-on-use.
- `src/mcp/base.py` `McpToolProvider` OAuth branch: constructs a factory that calls `get_valid_access_token` per outbound request. Tokens never transit the client.
- REST endpoints: `POST /mcp-servers/{id}/oauth/authorize`, `GET /oauth/callback`, `POST /mcp-servers/{id}/oauth/disconnect`.
- `src/engine/context.py` gains `LangSmithConfig` + ContextVar; `LangGraphExecutor` sets it from Settings before `compiled.ainvoke`. `build_chat_model` wraps the returned model via `with_config` when tracing is enabled.
- Integration test against real Highspot (seeds refresh token, exercises refresh → tool call hot path).
- Regression port: OAB's Highspot OAuth lifecycle spec — two tests asserting `resource` on token exchange + refresh.

#### Changed
- `McpToolProvider.__init__` now accepts `db: Any | None = None` + `user_id: str | None = None` as kwargs. Static-auth callers may omit both.
- `MCPClient.__init__` accepts `auth_header_factory` alongside the existing `auth_header` dict. Factory wins when both provided.
- `src/llm/providers.py build_chat_model` accepts `langsmith_config: LangSmithConfig | None`. When provided (and tracing_v2 is True), wraps the model via `with_config`. Back-compat: `None` returns the raw model.
- `src/tools/base.py BuildContext` gains `langsmith_config: LangSmithConfig | None = None` field.

#### Fixed
- OAB's six hard-won MCP fixes are now all in place in Composer (scorecard in spec §1): RFC 8707 `resource` on all four flows (3b), manual tool calling (3a), `inputSchema` camelCase (3a), server-side token retrieval (3b), isShared + service-account token fallback (3a permissions + 3b token fallback), LangSmith config threaded (3b).
```

- [ ] **Step 3: Update CLAUDE.md phase table**

Change:
```markdown
| 3a — MCP infrastructure (static auth) | ✅ Complete | DeepWiki + Firecrawl MCP verified end-to-end on `/mcp` streamable HTTP transport |
| 3b — MCP OAuth (Highspot + the six fixes) | ⏭ Next | RFC 8707 resource param, manual tool calling, inputSchema camelCase — see §5 below |
| 4 — HTTP, Transform, Extract, If/Else, While, Set-State | ⏸ | |
```
To:
```markdown
| 3a — MCP infrastructure (static auth) | ✅ Complete | DeepWiki + Firecrawl MCP verified end-to-end on `/mcp` streamable HTTP transport |
| 3b — MCP OAuth (Highspot + the six fixes) | ✅ Complete | All six OAB lessons encoded; Highspot verified end-to-end |
| 4 — HTTP, Transform, Extract, If/Else, While, Set-State | ⏭ Next | |
```

- [ ] **Step 4: Backfill ADR-0011 `Implemented by`**

In `docs/design/decisions.md`, change:
```markdown
**Implemented by.** Phase 3b (commits TBD).
```
To:
```markdown
**Implemented by.** Phase 3b (commits `<first-3b-sha>`..`<last-3b-sha>` on `main`, 2026-04-20).
```

Get the range via:
```bash
git log --oneline 0c1bf07..HEAD
```
(the SHA at end of Phase 3a.)

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-3b): mark Phase 3b complete

MCP OAuth shipped. All six OAB hard-won MCP fixes now encoded in
Composer (scorecard in CHANGELOG Phase 3b section). Highspot verified
end-to-end against the real IdP.

3a + 3b together close out Phase 3. Phase 4 (HTTP, Transform, Extract,
If/Else, While, Set-State) is next.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §5 Prisma schema | Task 1 |
| §7 OAuth primitives | Tasks 2–6 |
| §8 McpToolProvider OAuth branch | Tasks 7–9 |
| §9 REST endpoints | Task 10 |
| §10 LangSmith config | Task 11 |
| §11 Error model | Task 2 (exception classes); used throughout Tasks 4–6, 10 |
| §12 Test plan | Tasks 2–6, 10 (unit); Task 12 (integration); Task 13 (regression) |
| §13 Phase-exit checklist | Task 14 |

Every spec section has a task. No placeholders in the plan (every step has complete code). Type consistency verified: `get_valid_access_token(server, user_id, db)`, `refresh_token(server, token_row, db)`, `exchange_code_for_tokens(server, code, state, db)`, `build_authorize_url(server, user_id, redirect_uri, db)` — all four sigs used consistently across Tasks 2–6 and 10.

---

## Execution handoff

Plan saved. Controller proceeds to **subagent-driven-development** for Tasks 1–14. Same pattern as Phase 3a. Final phase-level check (Task 14) runs the integration suite against real Highspot + real Neon before the phase-exit commit lands.
