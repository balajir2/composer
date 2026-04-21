# Phase 7a — Deployment-mode toggle + auth middleware: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a single env var (`COMPOSER_DEPLOYMENT_MODE`) that selects standalone vs embedded auth. Standalone mode gets a `User` table, bcrypt passwords, and `/auth/register|login|refresh|disconnect` endpoints. Embedded mode validates IEP-signed JWTs. Dev-mode fallback keeps existing integration tests working. Migrate every `user_id="dev"` literal to `Depends(get_current_user_id)`.

**Architecture.** Single FastAPI dependency `get_current_user_id(request, settings) -> str` dispatches to one of two verifiers based on `settings.deployment_mode`. Router registration branches at startup: standalone registers `/auth/register|login|refresh|disconnect`; both modes register `/auth/me`. The `User` Prisma table exists in schema always but is populated only in standalone writes.

**Tech Stack:** Python 3.11/3.12, FastAPI, Prisma Python, bcrypt, PyJWT (Phase 1's `src/security/jwt.py` already uses it for HS256). JWKS/RS256 path is stubbed — shared-secret HS256 is the 7a embedded path; 7b adds JWKS.

**Spec:** [`docs/superpowers/specs/2026-04-21-phase-7a-deployment-mode-design.md`](../specs/2026-04-21-phase-7a-deployment-mode-design.md)
**ADRs:** [ADR-0014](../../design/decisions.md#adr-0014-deployment-mode-toggle--env-var-read-at-startup), [ADR-0015](../../design/decisions.md#adr-0015-dev-mode-auth-fallback--user_iddev-when-no-authorization-header-in-environmentdevelopment)

---

## Sequencing and discipline

11 tasks, one commit each. Every task ends green on:

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

Integration tests (Tasks 9 + 10) run at phase-exit (Task 11) with real Neon.

**⚠️ Forbidden files (all tasks except where explicitly noted):** `pyproject.toml` (except Task 2 if bcrypt dep needed), `.github/workflows/*`, `CLAUDE.md` (except Task 11), `docs/design/*` (except Task 11 for ADR backfill), `docs/superpowers/plans/*`, `docs/superpowers/specs/*`. Fix pyright strict errors with `# pyright: ignore[specific]` inline — never loosen `pyproject.toml`.

Commit footer every commit:
```
Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Stay on `main`. No feature branches.

---

## Task 1: Prisma schema — `User` table + migration

**Files:**
- Modify: `prisma/schema.prisma`
- Create: `prisma/migrations/<timestamp>_phase_7a_user_table/migration.sql` (generated)

**Authorized:** `prisma/schema.prisma` for this task.

- [ ] **Step 1: Append User + UserRole enum**

Append to `prisma/schema.prisma` after the existing McpOAuthState model:

```prisma
enum UserRole {
  admin
  member
}

model User {
  id            String    @id @default(cuid())
  email         String    @unique
  passwordHash  String    @map("password_hash")
  displayName   String?   @map("display_name")
  role          UserRole  @default(member)
  createdAt     DateTime  @default(now()) @map("created_at")
  updatedAt     DateTime  @updatedAt       @map("updated_at")

  @@map("users")
  @@index([email])
}
```

- [ ] **Step 2: Generate + migrate**

```bash
uv run prisma generate
uv run prisma migrate dev --name phase_7a_user_table
```

Fall back to `.venv/Scripts/python -m prisma <cmd>` if `uv` isn't on PATH. If Neon times out (cold start), use:
```bash
uv run prisma migrate deploy
```

- [ ] **Step 3: Verify**

```bash
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 0 pyright errors, 316 tests pass (same as Phase 4b exit).

- [ ] **Step 4: Commit**

```bash
git add prisma/schema.prisma prisma/migrations/
git commit -m "feat(schema): User table + UserRole enum (Phase 7a)

Standalone deployment mode populates this table via /auth/register.
Embedded deployments leave it empty — userId strings come from IEP-
signed JWT sub claims.

Fields:
  - email (unique, indexed)
  - passwordHash (bcrypt output; algorithm+rounds+salt+digest in one col)
  - displayName (optional)
  - role (admin | member enum; Phase 7a doesn't enforce RBAC — reserved)

See Phase 7a spec §5.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Settings + bcrypt dep

**Files:**
- Modify: `src/config.py`
- Modify: `tests/unit/test_config.py` (or similar — check if exists; add tests for new settings)
- Modify: `pyproject.toml` (add bcrypt) — **authorized for this task only**

- [ ] **Step 1: Check if bcrypt is already declared**

```bash
grep -i bcrypt pyproject.toml
```
If present → skip the `pyproject.toml` edit. If not present, add `"bcrypt>=4.0.0"` to the `[project].dependencies` list.

- [ ] **Step 2: Extend `src/config.py`**

Read the file. Add these fields to `class Settings(BaseSettings)`:

```python
    # ─── Deployment mode (Phase 7a, ADR-0014) ────────
    deployment_mode: Literal["standalone", "embedded"] = Field(
        default="standalone",
        description="'standalone' owns users + issues its own JWTs; 'embedded' trusts JWTs from IEP.",
    )

    # ─── Embedded-mode (IEP integration) ─────────────
    iep_jwt_issuer: str = Field(default="", description="Expected `iss` claim in IEP-issued JWTs.")
    iep_jwks_url: str = Field(default="", description="RS256: URL to fetch IEP's public keys (Phase 7b).")
    iep_shared_secret: str = Field(default="", description="HS256: shared secret with IEP (Phase 7a path).")
    iep_ui_origin: str = Field(default="", description="Exact origin allowed by CORS when embedded.")

    # ─── Standalone password hashing ─────────────────
    bcrypt_rounds: int = Field(default=12, description="bcrypt cost factor.")
```

Import `Literal` at the top of the file if not already imported.

- [ ] **Step 3: Install + regenerate lock**

```bash
uv sync --all-extras
```
If bcrypt was already a dep (likely — Phase 0 included it), skip.

- [ ] **Step 4: Add settings tests** (inline, small)

If `tests/unit/test_config.py` exists, append 4 tests. Otherwise create it:

```python
"""Tests for Settings fields added in Phase 7a."""

import pytest


def test_deployment_mode_defaults_standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSER_DEPLOYMENT_MODE", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().deployment_mode == "standalone"


def test_deployment_mode_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().deployment_mode == "embedded"


def test_deployment_mode_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "cloud")
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(Exception):  # pydantic ValidationError
        get_settings()


def test_bcrypt_rounds_default() -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().bcrypt_rounds == 12
```

- [ ] **Step 5: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_config.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/config.py tests/unit/test_config.py pyproject.toml uv.lock 2>/dev/null || git add src/config.py tests/unit/test_config.py
git commit -m "feat(config): deployment_mode + IEP integration fields + bcrypt_rounds

COMPOSER_DEPLOYMENT_MODE env var (ADR-0014): standalone | embedded.
Default 'standalone'.

Embedded-mode config (ignored in standalone): iep_jwt_issuer,
iep_jwks_url (Phase 7b), iep_shared_secret (Phase 7a), iep_ui_origin
(CORS allowlist).

bcrypt_rounds defaults to 12 — industry-standard cost factor.

See Phase 7a spec §6.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `src/security/auth.py` — `get_current_user_id` dependency

**Files:**
- Create: `src/security/auth.py`
- Create: `tests/unit/security/test_auth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/security/test_auth.py`:

```python
"""Tests for the auth middleware primitives (Phase 7a, ADR-0014 + 0015)."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.security.auth import (
    AuthError,
    _extract_bearer,  # pyright: ignore[reportPrivateUsage]
    _verify_embedded_jwt,  # pyright: ignore[reportPrivateUsage]
    _verify_standalone_jwt,  # pyright: ignore[reportPrivateUsage]
    get_current_user_id,
)


def _mock_request(auth_header: str | None = None) -> Any:
    req = MagicMock()
    req.headers.get = lambda key, default="": auth_header if key.lower() == "authorization" else default  # pyright: ignore[reportUnknownLambdaType]
    return req


def _mock_settings(
    *,
    deployment_mode: str = "standalone",
    environment: str = "production",
    jwt_secret: str = "test-secret",
    iep_shared_secret: str = "",
    iep_jwt_issuer: str = "",
    iep_jwks_url: str = "",
) -> Any:
    s = MagicMock()
    s.deployment_mode = deployment_mode
    s.environment = environment
    s.jwt_secret = jwt_secret
    s.iep_shared_secret = iep_shared_secret
    s.iep_jwt_issuer = iep_jwt_issuer
    s.iep_jwks_url = iep_jwks_url
    return s


def test_extract_bearer_happy() -> None:
    req = _mock_request("Bearer abc.def.ghi")
    assert _extract_bearer(req) == "abc.def.ghi"


def test_extract_bearer_missing() -> None:
    req = _mock_request(None)
    assert _extract_bearer(req) is None


def test_extract_bearer_empty() -> None:
    req = _mock_request("")
    assert _extract_bearer(req) is None


def test_extract_bearer_wrong_scheme() -> None:
    req = _mock_request("Basic dXNlcjpwYXNz")
    assert _extract_bearer(req) is None


async def test_verify_standalone_jwt_happy() -> None:
    from src.security.jwt import encode_access_token

    settings = _mock_settings()
    token = encode_access_token(
        subject="user-1",
        secret=settings.jwt_secret,
        ttl_seconds=3600,
    )
    user_id = await _verify_standalone_jwt(token, settings)
    assert user_id == "user-1"


async def test_verify_standalone_jwt_bad_signature() -> None:
    settings = _mock_settings(jwt_secret="wrong")
    with pytest.raises(AuthError):
        await _verify_standalone_jwt("not.a.jwt", settings)


async def test_verify_embedded_jwt_happy() -> None:
    from src.security.jwt import encode_access_token

    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret="iep-secret",
        iep_jwt_issuer="https://iep.test",
    )
    token = encode_access_token(
        subject="user-2",
        secret=settings.iep_shared_secret,
        ttl_seconds=3600,
        extra_claims={"iss": "https://iep.test"},
    )
    user_id = await _verify_embedded_jwt(token, settings)
    assert user_id == "user-2"


async def test_verify_embedded_jwt_wrong_issuer() -> None:
    from src.security.jwt import encode_access_token

    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret="iep-secret",
        iep_jwt_issuer="https://iep.test",
    )
    token = encode_access_token(
        subject="user-2",
        secret=settings.iep_shared_secret,
        ttl_seconds=3600,
        extra_claims={"iss": "https://evil.test"},
    )
    with pytest.raises(AuthError, match="issuer"):
        await _verify_embedded_jwt(token, settings)


async def test_verify_embedded_jwt_no_config_raises() -> None:
    settings = _mock_settings(deployment_mode="embedded")  # no iep_shared_secret
    with pytest.raises(AuthError, match="no IEP JWT verification configured"):
        await _verify_embedded_jwt("anything", settings)


async def test_get_current_user_id_dev_fallback() -> None:
    """ADR-0015: no Authorization + environment=development → 'dev'."""
    req = _mock_request(None)
    settings = _mock_settings(environment="development")
    user_id = await get_current_user_id(req, settings)
    assert user_id == "dev"


async def test_get_current_user_id_production_no_header_raises() -> None:
    req = _mock_request(None)
    settings = _mock_settings(environment="production")
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(req, settings)
    assert excinfo.value.status_code == 401


async def test_get_current_user_id_standalone_happy() -> None:
    from src.security.jwt import encode_access_token

    settings = _mock_settings(deployment_mode="standalone")
    token = encode_access_token(subject="u1", secret=settings.jwt_secret, ttl_seconds=3600)
    req = _mock_request(f"Bearer {token}")
    assert await get_current_user_id(req, settings) == "u1"


async def test_get_current_user_id_routes_to_embedded_verifier() -> None:
    from src.security.jwt import encode_access_token

    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret="iep-secret",
        iep_jwt_issuer="https://iep.test",
    )
    token = encode_access_token(
        subject="u2",
        secret=settings.iep_shared_secret,
        ttl_seconds=3600,
        extra_claims={"iss": "https://iep.test"},
    )
    req = _mock_request(f"Bearer {token}")
    assert await get_current_user_id(req, settings) == "u2"
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/security/test_auth.py -v
```
Expected: ImportError.

- [ ] **Step 3: Check `src/security/jwt.py` for signature helpers**

Read `src/security/jwt.py` (Phase 1). It should have `encode_access_token(subject, secret, ttl_seconds, extra_claims=None) -> str` and `decode_hs256_jwt(token, secret) -> dict[str, Any]`. If the signatures differ, adapt the tests' import + calls. If the `extra_claims` kwarg doesn't exist, either extend the jwt module (in-scope for this task — small, contained change) or drop the `iss` assertion path from the embedded verifier (trim the test).

- [ ] **Step 4: Implement `src/security/auth.py`**

```python
"""Auth middleware primitives for Phase 7a.

`get_current_user_id` is the FastAPI dependency every user-scoped route
uses.  It dispatches between standalone (HS256 with Composer's JWT_SECRET)
and embedded (IEP-signed JWT) verification based on `settings.deployment_mode`.

Dev-mode fallback (ADR-0015): when `environment=development` AND no
Authorization header is present, return user_id='dev' to keep existing
integration tests working.  Unreachable when `environment=production`.
"""

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from src.config import Settings, get_settings
from src.security.jwt import decode_hs256_jwt


class AuthError(HTTPException):
    """401 with a detail string — the only auth-layer exception we raise publicly."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _extract_bearer(request: Request) -> str | None:
    """Return the raw JWT from Authorization: Bearer <token>, or None."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[len("bearer "):].strip()
    return token or None


async def _verify_standalone_jwt(token: str, settings: Settings) -> str:
    """Decode HS256 JWT signed with Composer's own JWT_SECRET; return sub."""
    try:
        claims = decode_hs256_jwt(token, settings.jwt_secret)
    except Exception as exc:
        raise AuthError(f"invalid JWT: {exc}") from exc
    sub = claims.get("sub")
    if not sub:
        raise AuthError("JWT missing 'sub' claim")
    return str(sub)


async def _verify_embedded_jwt(token: str, settings: Settings) -> str:
    """Verify IEP-signed JWT.  7a: HS256 shared-secret path; 7b adds JWKS/RS256."""
    if not settings.iep_shared_secret:
        raise AuthError(
            "embedded mode: no IEP JWT verification configured "
            "(set IEP_JWKS_URL or IEP_SHARED_SECRET)"
        )
    try:
        claims = decode_hs256_jwt(token, settings.iep_shared_secret)
    except Exception as exc:
        raise AuthError(f"invalid IEP JWT: {exc}") from exc
    if settings.iep_jwt_issuer and claims.get("iss") != settings.iep_jwt_issuer:
        raise AuthError("IEP JWT issuer mismatch")
    sub = claims.get("sub")
    if not sub:
        raise AuthError("JWT missing 'sub' claim")
    return str(sub)


async def get_current_user_id(
    request: Request,
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> str:
    """FastAPI dependency — returns authenticated user_id.

    Dev-mode fallback (ADR-0015): if environment=development AND no
    Authorization header, return 'dev'.  Production never hits the fallback.
    """
    token = _extract_bearer(request)
    if token is None:
        if settings.environment == "development":
            return "dev"
        raise AuthError("missing Authorization header")

    if settings.deployment_mode == "embedded":
        return await _verify_embedded_jwt(token, settings)
    return await _verify_standalone_jwt(token, settings)


__all__ = ["AuthError", "get_current_user_id"]
```

- [ ] **Step 5: If `encode_access_token` lacks `extra_claims`, extend `src/security/jwt.py`**

Read the file. If the signature is `encode_access_token(subject, secret, ttl_seconds) -> str`, extend it:

```python
def encode_access_token(
    subject: str,
    secret: str,
    ttl_seconds: int,
    *,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Encode an HS256 JWT with sub, iat, exp, plus optional extra claims."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm="HS256")
```

Keep the function back-compat (positional args unchanged). If Phase 1's signature is different from what I sketched, match its existing shape — this is a surgical extension.

- [ ] **Step 6: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/security/test_auth.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 13 new auth tests pass; overall +13 (~333, plus any Task 2 config tests).

```bash
git add src/security/auth.py tests/unit/security/test_auth.py
# include src/security/jwt.py if you extended it
git commit -m "feat(security): auth middleware — get_current_user_id dependency

Dispatches between _verify_standalone_jwt (HS256 w/ Composer's secret)
and _verify_embedded_jwt (HS256 w/ IEP_SHARED_SECRET) based on
settings.deployment_mode.

Dev-mode fallback (ADR-0015): when environment=development AND no
Authorization header, return 'dev'.  Production never hits this path.

AuthError (401) is the only exception raised publicly — all failure
paths map to it.

See Phase 7a spec §7, ADR-0014, ADR-0015.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Bcrypt helpers

**Files:**
- Create: `src/security/passwords.py`
- Create: `tests/unit/security/test_passwords.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/security/test_passwords.py`:

```python
"""Tests for bcrypt password helpers."""

import pytest

from src.security.passwords import hash_password, verify_password


def test_hash_and_verify_round_trip() -> None:
    h = hash_password("correct-horse-battery-staple")
    assert isinstance(h, str)
    assert h.startswith("$2")  # bcrypt identifier prefix
    assert verify_password("correct-horse-battery-staple", h) is True


def test_verify_wrong_password() -> None:
    h = hash_password("password123")
    assert verify_password("password456", h) is False


def test_hash_is_randomized() -> None:
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2


def test_verify_malformed_hash_returns_false() -> None:
    # Invalid hash should not raise — return False so timing side channels
    # don't distinguish "user doesn't exist" from "wrong password"
    assert verify_password("any-pass", "not-a-bcrypt-hash") is False
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/security/test_passwords.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/security/passwords.py`**

```python
"""Bcrypt password hashing helpers.

bcrypt ships its own salt+rounds identification in the output hash
(`$2b$<rounds>$<salt><digest>` format); one column stores everything.

Used ONLY by standalone-mode /auth/register and /auth/login.
"""

import bcrypt

from src.config import get_settings


def hash_password(plaintext: str) -> str:
    """Hash a password with bcrypt using settings.bcrypt_rounds cost factor."""
    rounds = get_settings().bcrypt_rounds
    salted = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=rounds))
    return salted.decode("utf-8")


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """Constant-time comparison.  Malformed hash → False (no exception)."""
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


__all__ = ["hash_password", "verify_password"]
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/security/test_passwords.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/security/passwords.py tests/unit/security/test_passwords.py
git commit -m "feat(security): bcrypt password hash + verify helpers

One-column bcrypt: algorithm+rounds+salt+digest in the stored hash.
Cost factor read from settings.bcrypt_rounds (default 12).

verify_password returns False on malformed hash rather than raising —
keeps the timing side channel uniform so attackers can't distinguish
'user doesn't exist' (malformed empty hash) from 'wrong password'.

Used only in standalone mode /auth/register + /auth/login.

See Phase 7a spec §8.3.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Standalone auth router — `/auth/register`, `/login`, `/refresh`, `/disconnect`

**Files:**
- Create: `src/api/auth_standalone.py`
- Create: `tests/unit/api/test_auth_standalone.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/api/test_auth_standalone.py`:

```python
"""Tests for /auth/register, /login, /refresh, /disconnect (standalone mode)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _user_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "u1",
        "email": "alice@example.com",
        "passwordHash": "$2b$12$placeholder",
        "displayName": "Alice",
        "role": "member",
        "createdAt": "2026-04-21T00:00:00Z",
        "updatedAt": "2026-04-21T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client_standalone(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    db.user.create = AsyncMock(return_value=_user_row())
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


def test_register_creates_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "correct-horse", "displayName": "Alice"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert "accessToken" in body
    assert "refreshToken" in body
    db.user.create.assert_awaited_once()
    # Ensure we hashed the password (not stored plaintext)
    call = db.user.create.await_args
    created = call.kwargs["data"]
    assert created["passwordHash"] != "correct-horse"
    assert created["passwordHash"].startswith("$2")


def test_register_duplicate_email_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row())  # email taken
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "x"},
    )
    assert resp.status_code == 409


def test_login_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
    from src.security.passwords import hash_password

    db.user.find_unique = AsyncMock(
        return_value=_user_row(passwordHash=hash_password("right-pass"))
    )
    resp = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "right-pass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "accessToken" in body
    assert "refreshToken" in body


def test_login_wrong_password_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
    from src.security.passwords import hash_password

    db.user.find_unique = AsyncMock(
        return_value=_user_row(passwordHash=hash_password("right-pass"))
    )
    resp = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "wrong-pass"},
    )
    assert resp.status_code == 401


def test_login_unknown_email_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=None)
    resp = client.post(
        "/auth/login",
        json={"email": "ghost@example.com", "password": "x"},
    )
    assert resp.status_code == 401


def test_refresh_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
    from src.security.jwt import encode_refresh_token

    from src.config import get_settings

    settings = get_settings()
    refresh = encode_refresh_token(subject="u1", secret=settings.jwt_secret, ttl_seconds=604800)
    db.user.find_unique = AsyncMock(return_value=_user_row(id="u1"))
    resp = client.post("/auth/refresh", json={"refreshToken": refresh})
    assert resp.status_code == 200
    body = resp.json()
    assert "accessToken" in body
    assert "refreshToken" in body


def test_disconnect_returns_204(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_standalone(monkeypatch)
    resp = client.post("/auth/disconnect")
    assert resp.status_code == 204


def test_embedded_mode_auth_register_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In embedded mode, /auth/register route is not registered → 404."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_SHARED_SECRET", "test")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.db = MagicMock()
    app.state.checkpointer = MagicMock()
    client = TestClient(app)
    resp = client.post("/auth/register", json={"email": "x@y.z", "password": "x"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_auth_standalone.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/api/auth_standalone.py`**

```python
"""Standalone-mode /auth/* endpoints.

Registered only when settings.deployment_mode == 'standalone'.
In embedded mode the router isn't included, so these routes 404.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.config import Settings, get_settings
from src.security.jwt import decode_hs256_jwt, encode_access_token, encode_refresh_token
from src.security.passwords import hash_password, verify_password
from src.storage.db import get_db

router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str | None = Field(default=None, alias="displayName")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    refresh_token: str = Field(alias="refreshToken")


class AuthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    email: str
    display_name: str | None = Field(default=None, alias="displayName")
    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")


class TokenPairResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")


def _issue_pair(user_id: str, settings: Settings) -> tuple[str, str]:
    access = encode_access_token(
        subject=user_id, secret=settings.jwt_secret, ttl_seconds=settings.jwt_access_ttl_seconds
    )
    refresh = encode_refresh_token(
        subject=user_id, secret=settings.jwt_secret, ttl_seconds=settings.jwt_refresh_ttl_seconds
    )
    return access, refresh


@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> AuthResponse:
    existing = await db.user.find_unique(where={"email": payload.email})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email already registered"
        )
    user = await db.user.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "email": str(payload.email),
            "passwordHash": hash_password(payload.password),
            "displayName": payload.display_name,
        }
    )
    access, refresh = _issue_pair(user.id, settings)
    return AuthResponse(
        id=user.id,
        email=user.email,
        display_name=user.displayName,
        access_token=access,
        refresh_token=refresh,
    )


@router.post("/auth/login", response_model=TokenPairResponse)
async def login(
    payload: LoginRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> TokenPairResponse:
    user = await db.user.find_unique(where={"email": str(payload.email)})  # pyright: ignore[reportAttributeAccessIssue]
    # Uniform 401: don't distinguish unknown-email from wrong-password
    if user is None or not verify_password(payload.password, user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        )
    access, refresh = _issue_pair(user.id, settings)
    return TokenPairResponse(access_token=access, refresh_token=refresh)


@router.post("/auth/refresh", response_model=TokenPairResponse)
async def refresh(
    payload: RefreshRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> TokenPairResponse:
    try:
        claims = decode_hs256_jwt(payload.refresh_token, settings.jwt_secret)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid refresh token: {exc}"
        ) from exc
    user_id = str(claims.get("sub", ""))
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token missing sub"
        )
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user no longer exists"
        )
    access, new_refresh = _issue_pair(user.id, settings)
    return TokenPairResponse(access_token=access, refresh_token=new_refresh)


@router.post("/auth/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect() -> None:
    """Client-side token drop.  Server-side revocation lands in Phase 7b."""
    return None


__all__ = [
    "AuthResponse",
    "LoginRequest",
    "RefreshRequest",
    "RegisterRequest",
    "TokenPairResponse",
    "router",
]
```

**Note on `encode_refresh_token`:** if `src/security/jwt.py` has only `encode_access_token`, add `encode_refresh_token` as a thin wrapper — same shape but could add a `"typ": "refresh"` claim for future-proofing. Adjust the test helper's import accordingly if the function is named differently.

**Note on `EmailStr`:** requires `pydantic[email]` — the `email-validator` package. If `pyproject.toml` doesn't already have it, add it to Task 2's dep list alongside bcrypt.

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_auth_standalone.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```
Expected: 8 new pass.

```bash
git add src/api/auth_standalone.py tests/unit/api/test_auth_standalone.py
# include src/security/jwt.py if you extended it (added encode_refresh_token)
git commit -m "feat(api): /auth/register, /login, /refresh, /disconnect (standalone)

Four endpoints, all Pydantic-validated request/response:
  - POST /auth/register: email uniqueness → 409; bcrypt password hash;
    returns access+refresh tokens on success (201)
  - POST /auth/login: uniform 401 on wrong email OR wrong password
    (no timing side channel for user-exists distinction)
  - POST /auth/refresh: rotates both tokens; 401 on invalid/expired
    refresh or deleted user
  - POST /auth/disconnect: 204; server-side revocation in Phase 7b

Only registered in standalone mode.  Embedded-mode router simply
doesn't include these routes → 404.

See Phase 7a spec §8.1.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Common auth router — `/auth/me`

**Files:**
- Create: `src/api/auth_common.py`
- Create: `tests/unit/api/test_auth_common.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/api/test_auth_common.py`:

```python
"""Tests for /auth/me (both modes)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _client(monkeypatch: pytest.MonkeyPatch, *, mode: str = "standalone") -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", mode)
    monkeypatch.setenv("ENVIRONMENT", "production")
    if mode == "embedded":
        monkeypatch.setenv("IEP_SHARED_SECRET", "iep-secret")
        monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="u1",
            email="alice@example.com",
            displayName="Alice",
            role="member",
        )
    )
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


def test_me_standalone_returns_user_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings
    from src.security.jwt import encode_access_token

    client, _ = _client(monkeypatch, mode="standalone")
    settings = get_settings()
    token = encode_access_token(subject="u1", secret=settings.jwt_secret, ttl_seconds=3600)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "u1"
    assert body["email"] == "alice@example.com"
    assert body["displayName"] == "Alice"
    assert body["role"] == "member"


def test_me_embedded_returns_jwt_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings
    from src.security.jwt import encode_access_token

    client, _ = _client(monkeypatch, mode="embedded")
    settings = get_settings()
    token = encode_access_token(
        subject="external-user",
        secret=settings.iep_shared_secret,
        ttl_seconds=3600,
        extra_claims={"iss": "https://iep.test"},
    )
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "external-user"
    assert "claims" in body


def test_me_missing_auth_401_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, mode="standalone")
    resp = client.get("/auth/me")
    assert resp.status_code == 401
```

- [ ] **Step 2: Verify fail**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_auth_common.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/api/auth_common.py`**

```python
"""Auth endpoints common to both standalone and embedded modes.

Currently just /auth/me.  Registered in both modes.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from pydantic import BaseModel, ConfigDict, Field

from src.config import Settings, get_settings
from src.security.auth import get_current_user_id
from src.security.jwt import decode_hs256_jwt
from src.storage.db import get_db

router = APIRouter(tags=["auth"])


class StandaloneMeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    email: str
    display_name: str | None = Field(default=None, alias="displayName")
    role: str


class EmbeddedMeResponse(BaseModel):
    id: str
    claims: dict[str, Any]


@router.get("/auth/me")
async def me(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> StandaloneMeResponse | EmbeddedMeResponse:
    if settings.deployment_mode == "standalone":
        user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
        return StandaloneMeResponse(
            id=user.id, email=user.email, display_name=user.displayName, role=user.role
        )
    # embedded: return JWT claims directly
    header = request.headers.get("authorization", "")
    token = header[len("bearer "):].strip() if header.lower().startswith("bearer ") else ""
    if token and settings.iep_shared_secret:
        try:
            claims = decode_hs256_jwt(token, settings.iep_shared_secret)
        except Exception:
            claims = {}
    else:
        claims = {}
    return EmbeddedMeResponse(id=user_id, claims=claims)


__all__ = ["EmbeddedMeResponse", "StandaloneMeResponse", "router"]
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_auth_common.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/api/auth_common.py tests/unit/api/test_auth_common.py
git commit -m "feat(api): /auth/me — works in both deployment modes

Standalone: reads User row by id → returns {id, email, displayName, role}.
Embedded: returns {id: sub, claims: <JWT claims dict>} without touching
the User table.

Registered in both modes (unlike the standalone-only register/login).

See Phase 7a spec §8.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Router registration in `src/main.py` + mode-based CORS

**Files:**
- Modify: `src/main.py`
- Create (or extend): `tests/unit/test_main.py`

- [ ] **Step 1: Read and understand current `src/main.py`**

It has `create_app()` that registers workflows_router, executions_router, mcp_servers_router, oauth_router. Needs:
1. Mode-based CORS allowlist
2. Conditional `auth_standalone_router` registration (standalone only)
3. `auth_common_router` registration (both modes)
4. Startup validation: embedded mode requires `IEP_SHARED_SECRET` or `IEP_JWKS_URL` + `IEP_JWT_ISSUER`; refuse to boot otherwise

- [ ] **Step 2: Modify `src/main.py`**

At the top, import the new routers:
```python
from src.api.auth_common import router as auth_common_router
from src.api.auth_standalone import router as auth_standalone_router
```

In `create_app()`, after the existing routers:

```python
    # Phase 7a: auth
    if settings.deployment_mode == "standalone":
        app.include_router(auth_standalone_router)
    app.include_router(auth_common_router)
```

Before that, update the CORS config:

```python
    # CORS: mode-aware
    if settings.deployment_mode == "embedded":
        allow_origins = [settings.iep_ui_origin] if settings.iep_ui_origin else []
    else:
        allow_origins = ["*"] if settings.environment == "development" else []

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

At the start of `create_app()`, add the mode-sanity check:

```python
    # ADR-0014: fail fast if embedded mode is misconfigured
    if settings.deployment_mode == "embedded":
        if not settings.iep_jwt_issuer:
            raise RuntimeError(
                "COMPOSER_DEPLOYMENT_MODE=embedded requires IEP_JWT_ISSUER"
            )
        if not (settings.iep_shared_secret or settings.iep_jwks_url):
            raise RuntimeError(
                "COMPOSER_DEPLOYMENT_MODE=embedded requires IEP_SHARED_SECRET or IEP_JWKS_URL"
            )

    # ADR-0015: warn loudly if dev-mode auth fallback is enabled
    if settings.environment == "development":
        logger.warning(
            "auth: dev-mode fallback ENABLED (user_id='dev' when no Authorization header)"
        )
```

- [ ] **Step 3: Add tests**

Append to (or create) `tests/unit/test_main.py`:

```python
"""Tests for create_app()'s mode-aware behavior (Phase 7a)."""

import pytest

from src.main import create_app


def test_standalone_includes_auth_register_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    paths = {r.path for r in app.routes}  # pyright: ignore[reportAttributeAccessIssue]
    assert "/auth/register" in paths
    assert "/auth/me" in paths


def test_embedded_excludes_auth_register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    paths = {r.path for r in app.routes}  # pyright: ignore[reportAttributeAccessIssue]
    assert "/auth/register" not in paths
    assert "/auth/login" not in paths
    assert "/auth/me" in paths  # common route stays


def test_embedded_missing_issuer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret")
    monkeypatch.delenv("IEP_JWT_ISSUER", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="IEP_JWT_ISSUER"):
        create_app()


def test_embedded_missing_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.delenv("IEP_SHARED_SECRET", raising=False)
    monkeypatch.delenv("IEP_JWKS_URL", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="IEP_SHARED_SECRET"):
        create_app()
```

- [ ] **Step 4: Run + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_main.py -v
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
```

```bash
git add src/main.py tests/unit/test_main.py
git commit -m "feat(main): mode-aware router registration + CORS + startup checks

create_app() now:
  - Registers auth_standalone_router ONLY when deployment_mode=standalone
  - Registers auth_common_router in both modes
  - CORS: embedded → [iep_ui_origin]; standalone+dev → ['*'];
    standalone+prod → [] (to be filled in by Phase 7b)
  - Fails fast at boot if embedded mode has missing IEP_JWT_ISSUER or
    both of IEP_SHARED_SECRET+IEP_JWKS_URL missing (ADR-0014)
  - Logs prominent WARN when environment=development announcing the
    ADR-0015 dev-mode auth fallback is active

See Phase 7a spec §8 + §10.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Migrate route handlers off `user_id="dev"` hardcoding

**Files:**
- Modify: `src/api/mcp_servers.py` (5-6 handlers)
- Modify: `src/executors/agent.py`
- Modify: `src/executors/mcp.py`
- Modify: `src/engine/langgraph_executor.py` (propagate workflow_execution.userId into state)
- Modify: `src/engine/state.py` (WorkflowStateDict gains `user_id: NotRequired[str]`)
- Modify existing unit tests if they break

- [ ] **Step 1: Extend `WorkflowStateDict`**

Read `src/engine/state.py`. Add `user_id: NotRequired[str]` alongside existing fields. `NotRequired` is from `typing` (3.11+).

- [ ] **Step 2: Propagate in `LangGraphExecutor.run`**

Read `src/engine/langgraph_executor.py`. In `run()`, after loading `execution`, extract `execution.userId` and inject into the initial state:

```python
            state = initial_state(execution.input if execution.input is not None else "")
            if execution.userId:
                state["user_id"] = execution.userId
```

- [ ] **Step 3: Update `src/executors/agent.py` + `src/executors/mcp.py`**

Find `user_id="dev"` in both. Change to:
```python
user_id = state.get("user_id") or "dev"
```
Then `BuildContext(..., user_id=user_id, ...)`. The `"dev"` fallback covers in-progress runs that predate Phase 7a (checkpoints on disk still use old state shape); post-7a runs get real user IDs.

- [ ] **Step 4: Update `src/api/mcp_servers.py`**

Every route handler that currently hardcodes `"dev"` (grep to find them all — `create_mcp_server`, `list_mcp_servers`, `test_mcp_connection`, `delete_mcp_server`, `oauth_authorize`, `oauth_disconnect`) accepts:

```python
user_id: str = Depends(get_current_user_id),
```

And uses `user_id` in:
- `db.mcpserver.create(data={..., "userId": user_id, ...})`
- `db.mcpserver.find_many(where={"OR": [{"userId": user_id}, {"isShared": True}]})`
- `db.mcpoauthtoken.find_unique(where={"mcpServerId_userId": {"mcpServerId": server_id, "userId": user_id}})`
- etc.

Add the import at the top:
```python
from src.security.auth import get_current_user_id
```

- [ ] **Step 5: Run + fix regressions**

```bash
.venv/Scripts/python -m pytest -v --no-cov
```

Fix any broken unit tests. Most MCP server tests use `app.state.db` with a MagicMock — they don't set Authorization headers. Via ADR-0015, in dev-mode they'll hit the `"dev"` fallback and continue passing. If a test sets `ENVIRONMENT=production` without auth, it should get 401 — update the test to either set env var to `development` or add a real Authorization header.

- [ ] **Step 6: Commit**

```bash
git add src/api/mcp_servers.py src/executors/agent.py src/executors/mcp.py \
        src/engine/langgraph_executor.py src/engine/state.py \
        tests/unit/api/ tests/unit/executors/  # only if anything changed
git commit -m "refactor: replace user_id='dev' hardcodes with Depends(get_current_user_id)

Every route handler that scoped writes to user_id='dev' now declares
user_id: str = Depends(get_current_user_id).  Dev-mode fallback
(ADR-0015) returns 'dev' when no Authorization header + ENV=development,
so existing integration tests keep passing.

Executors (Agent, MCP) read user_id from state['user_id'] which
LangGraphExecutor.run populates from WorkflowExecution.userId.

WorkflowStateDict gains user_id: NotRequired[str].  Existing workflows
without it fall through to 'dev' in the executors — maintains checkpoint
compatibility for in-progress runs.

See Phase 7a spec §9.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Integration — standalone auth lifecycle + embedded JWT

**File:**
- Create: `tests/integration/test_standalone_auth_lifecycle.py`
- Create: `tests/integration/test_embedded_auth.py`

- [ ] **Step 1: Write `test_standalone_auth_lifecycle.py`**

Full lifecycle against real Neon: register → login → authenticated API call → refresh → me.

```python
"""Integration — standalone auth happy path against real Neon."""

import secrets

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_standalone_register_login_me_refresh(client: AsyncClient) -> None:
    # Uniqueify email to avoid colliding with prior runs
    email = f"test-{secrets.token_hex(6)}@composer.test"
    password = "correct-horse-battery-staple"

    # Register
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "displayName": "Integration"},
    )
    assert reg.status_code == 201, reg.text
    body = reg.json()
    access = body["accessToken"]
    refresh = body["refreshToken"]
    user_id = body["id"]

    try:
        # Authenticated /auth/me
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 200, me.text
        assert me.json()["email"] == email

        # Refresh
        r = await client.post("/auth/refresh", json={"refreshToken": refresh})
        assert r.status_code == 200, r.text
        new_access = r.json()["accessToken"]

        # New access works
        me2 = await client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        assert me2.status_code == 200

        # Login with the same credentials
        login = await client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200

        # Wrong password
        bad = await client.post("/auth/login", json={"email": email, "password": "wrong"})
        assert bad.status_code == 401
    finally:
        # Cleanup: delete the User row directly.  Disconnect endpoint doesn't
        # revoke server-side (Phase 7b); we clean up in Prisma.
        try:
            # We don't expose a DELETE /users endpoint, so use the app's db
            from httpx._transports.asgi import ASGITransport  # pyright: ignore[reportMissingTypeStubs, reportAttributeAccessIssue]

            transport: ASGITransport = client._transport  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType, reportAttributeAccessIssue]
            app = transport.app  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
            await app.state.db.user.delete(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
        except Exception:
            pass  # best-effort cleanup
```

- [ ] **Step 2: Write `test_embedded_auth.py`**

Mock-IEP JWT test. Requires `IEP_SHARED_SECRET` + `IEP_JWT_ISSUER` in env, plus `COMPOSER_DEPLOYMENT_MODE=embedded`. Since those default to unset, this test needs env setup — and the conftest's `app` fixture reads env at app-start. Simplest approach: the test manipulates env, clears `get_settings` cache, and calls `create_app()` directly rather than using the `client` fixture. Then uses a local `TestClient`.

Keep this one simpler — focus on proving the verifier works against a real app:

```python
"""Integration — embedded-mode JWT against real Neon."""

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.jwt import encode_access_token

pytestmark = pytest.mark.integration


def test_embedded_accepts_iep_signed_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_SHARED_SECRET", "test-iep-secret-phase-7a")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep-test.composer")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.db = MagicMock()  # /auth/me in embedded doesn't touch db
    app.state.checkpointer = MagicMock()

    token = encode_access_token(
        subject="iep-user-1",
        secret="test-iep-secret-phase-7a",
        ttl_seconds=3600,
        extra_claims={"iss": "https://iep-test.composer"},
    )

    with TestClient(app) as client:
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "iep-user-1"
        assert "claims" in body

        # Wrong-issuer token → 401
        bad_token = encode_access_token(
            subject="iep-user-1",
            secret="test-iep-secret-phase-7a",
            ttl_seconds=3600,
            extra_claims={"iss": "https://evil.test"},
        )
        bad = client.get("/auth/me", headers={"Authorization": f"Bearer {bad_token}"})
        assert bad.status_code == 401

        # No header in production → 401
        no_auth = client.get("/auth/me")
        assert no_auth.status_code == 401
```

- [ ] **Step 3: Quality gates + commit**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
.venv/Scripts/python -m pytest tests/integration/test_standalone_auth_lifecycle.py tests/integration/test_embedded_auth.py --collect-only -q
```

```bash
git add tests/integration/test_standalone_auth_lifecycle.py tests/integration/test_embedded_auth.py
git commit -m "test(integration): standalone auth lifecycle + embedded JWT

Standalone: register → authenticated /auth/me → refresh → /me → login
→ wrong-password 401.  Real Neon; unique-email-per-run for isolation.

Embedded: monkeypatched mode + IEP_SHARED_SECRET, create_app directly,
then hit /auth/me with:
  - IEP-signed JWT (correct issuer)  → 200
  - IEP-signed JWT (wrong issuer)    → 401
  - no Authorization header          → 401 (no dev fallback in production)

See Phase 7a spec §12.2.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Reserved — real-Neon integration fix-up

If the integration suite reveals bugs (schema mismatches, Prisma Json wrapping, etc. — the same pattern we saw in Phases 3a/3b/4a/4b), fix in a dedicated commit titled `"fix(phase-7a): <N> bugs caught by real-API integration testing"`.

If no bugs surface, skip.

---

## Task 11: Phase-exit — CHANGELOG + CLAUDE.md + ADR-0014/0015 backfill

**Authorized:** `CHANGELOG.md`, `CLAUDE.md`, `docs/design/decisions.md`.

- [ ] **Step 1: Verify exit checklist**

```bash
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m ruff format --check src tests
.venv/Scripts/python -m pyright src tests
.venv/Scripts/python -m pytest -m "not integration"
grep -rn 'user_id="dev"' src/ | grep -v 'src/security/auth.py'
```
The grep should return nothing (or only a commented line inside `auth.py`).

Run the integration suite (controller, not subagent):
```bash
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_standalone_auth_lifecycle.py',
     'tests/integration/test_embedded_auth.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```

Also run a subset of existing integration tests to verify dev-mode fallback keeps Phase 3a/3b/4a/4b working:

```bash
.venv/Scripts/python -c "
from dotenv import load_dotenv
import os, subprocess, sys
load_dotenv('.env', override=True)
os.environ['TEST_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
os.environ['ENVIRONMENT'] = 'development'  # ensure fallback is active
r = subprocess.run(
    [sys.executable, '-m', 'pytest', '-v', '--tb=short', '--no-cov',
     '-m', 'integration',
     'tests/integration/test_mcp_deepwiki.py',
     'tests/integration/test_linear_multinode.py',
     'tests/integration/test_if_else_routing.py',
     'tests/integration/test_while_countdown.py'],
    cwd='.'
)
sys.exit(r.returncode)
"
```

All pass → proceed.

- [ ] **Step 2: Update CHANGELOG.md**

Insert above the Phase 4b section:

```markdown
### Phase 7a — Deployment-mode toggle + auth middleware (2026-04-21)

Brought forward from Phase 7 because user-approval (Phase 5) needs authenticated user context.

#### Added
- [Phase 7a design spec](docs/superpowers/specs/2026-04-21-phase-7a-deployment-mode-design.md) + ADR-0014 + ADR-0015.
- Prisma `User` table + `UserRole` enum; populated only in standalone deployments.
- `src/config.py` — `deployment_mode`, `iep_jwt_issuer`, `iep_jwks_url`, `iep_shared_secret`, `iep_ui_origin`, `bcrypt_rounds`.
- `src/security/auth.py` — `AuthError`, `get_current_user_id` FastAPI dependency with mode-aware dispatch + dev-mode fallback (ADR-0015).
- `src/security/passwords.py` — bcrypt hash + verify helpers.
- `src/api/auth_standalone.py` — `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/disconnect` (registered only in standalone).
- `src/api/auth_common.py` — `/auth/me` (registered in both modes).
- 4 integration tests: standalone register→login→me→refresh lifecycle; embedded-mode JWT (correct issuer → 200; wrong issuer / no header → 401); existing Phase 3/4 integration tests continue to pass via dev-mode fallback.

#### Changed
- `src/main.py` — mode-aware router registration + CORS allowlist + startup validation. Embedded mode with missing `IEP_JWT_ISSUER` or both of `IEP_SHARED_SECRET`/`IEP_JWKS_URL` refuses to boot.
- `src/engine/state.py` — `WorkflowStateDict` gains `user_id: NotRequired[str]`.
- `src/engine/langgraph_executor.py` — propagates `execution.userId` into initial state.
- Every route handler and executor that hardcoded `user_id="dev"` now declares `Depends(get_current_user_id)` or reads `state["user_id"]`.
- Phase 5 (user-approval + SSE) is re-sequenced to run after 7a.

#### Fixed
- *(To be backfilled by Task 10 if any bugs surface during real-Neon verification.)*

#### Deliberate design choices (see ADR-0014 + ADR-0015)
- Deployment mode read from env var at startup; no live-switch. Auth semantics differ too fundamentally between modes for live-flipping to be safe.
- Dev-mode fallback (`user_id="dev"` when no Authorization + `environment=development`) keeps 15+ existing integration tests working without JWT refactor. Production deploys (`ENVIRONMENT=production`) get no fallback.
- 7a ships HS256-shared-secret path for IEP JWT verification; JWKS/RS256 is Phase 7b.
- Onboarding UI (Phase 10) activates when `deployment_mode=standalone`.
```

- [ ] **Step 3: Update `CLAUDE.md` phase table**

Change:
```markdown
| 5 — User-approval + SSE streaming | ⏭ Next | LangGraph `interrupt()` |
| ...
| 7 — API parity + regression suite ported | ⏸ | |
```
To:
```markdown
| 7a — Deployment-mode toggle + auth middleware | ✅ Complete | standalone vs embedded; User table; dev-mode fallback (ADR-0015) |
| 5 — User-approval + SSE streaming | ⏭ Next | LangGraph `interrupt()` — now has authenticated user context thanks to 7a |
| ...
| 7b — Security hardening (JWKS, rate limiting, CSRF, revocation) | ⏸ | |
```

- [ ] **Step 4: Backfill ADR-0014 + ADR-0015 `Implemented by`**

Both ADRs in `docs/design/decisions.md` currently say `(commits TBD)`. Replace with:
```markdown
**Implemented by.** Phase 7a (commits `<first>`..`<last>` on `main`, 2026-04-21).
```

Get the range via:
```bash
git log --oneline bfbf95d..HEAD
```
(starts just after Phase 4b exit.)

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/design/decisions.md
git commit -m "docs(phase-7a): mark Phase 7a complete

Deployment-mode toggle + auth middleware shipped.  standalone mode owns
users via Prisma User table + /auth/register|login|refresh|disconnect.
embedded mode trusts IEP-signed JWTs (HS256 shared secret; JWKS/RS256
deferred to 7b).

Dev-mode fallback (ADR-0015) preserves 15+ existing integration tests
without requiring JWT headers.

Phase 5 (user-approval + SSE) is re-sequenced to run next — it now
has authenticated user context thanks to 7a's get_current_user_id.

ADRs 0014 + 0015 Implemented by backfilled.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Spec coverage self-review

| Spec section | Tasks |
|---|---|
| §5 Prisma User table | Task 1 |
| §6 Settings additions | Task 2 |
| §7 auth.py primitives | Task 3 |
| §8.1 standalone router | Task 5 |
| §8.2 common router (/auth/me) | Task 6 |
| §8.3 bcrypt helpers | Task 4 |
| §9 migrate user_id="dev" | Task 8 |
| §10 CORS + origin | Task 7 |
| §12.1 unit tests | Tasks 2, 3, 4, 5, 6, 7 |
| §12.2 integration | Task 9 |
| §12.3 backward compat | Task 11 (phase-exit verification) |
| §13 phase-exit checklist | Task 11 |

No placeholder steps. Type consistency verified: `get_current_user_id(request, settings) → str` signature used identically across all route handlers; `AuthError` is the single 401 class.

---

## Execution handoff

Plan saved. Controller proceeds to `superpowers:subagent-driven-development` for Tasks 1–11. Same cadence as Phases 1–4b. Task 11 runs the integration suite (new + regression subset) against real Neon before phase-exit lands.
