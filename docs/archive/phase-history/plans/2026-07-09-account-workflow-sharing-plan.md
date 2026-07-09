# Account + Workflow Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship admin-forced + self-service password reset, many-to-many workflow assignment (sharing), and an autosave safety net that prevents silent Designer data loss.

**Architecture:** Three independent slices sharing one Prisma migration cadence. (A) A third JWT type (`password_change`) alongside the existing `access`/`refresh` discriminator gates a new `/auth/change-password` endpoint without touching any other route's authorization. (B) A `WorkflowAssignment` join table sits alongside the existing single-owner `Workflow.userId`, additive and non-destructive; a new `/users/search` endpoint lets non-admin owners find people to share with. (C) A small `useAutosave` hook wraps the Designer's existing manual-save mutation with a debounce + status state machine, reusing the current `PUT /workflows/{id}` path — no new backend surface.

**Tech Stack:** FastAPI + Prisma Python (backend), Next.js 14 + NextAuth v5 + TanStack Query + shadcn/ui (frontend), pytest + pytest-asyncio (backend tests), vitest + @testing-library/react (frontend tests).

**Spec:** `docs/archive/phase-history/specs/2026-07-09-account-workflow-sharing-design.md`

---

## Part A — Password reset

### Task A1: Schema — `mustChangePassword` on `User`

**Files:**
- Modify: `prisma/schema.prisma:170-184` (`User` model)

- [ ] **Step 1: Add the field**

In `prisma/schema.prisma`, the `User` model currently reads:

```prisma
model User {
  id            String    @id @default(cuid())
  email         String    @unique
  passwordHash  String?   @map("password_hash")
  displayName   String?   @map("display_name")
  role          UserRole  @default(member)
  isActive      Boolean   @default(true) @map("is_active")
  createdAt     DateTime  @default(now()) @map("created_at")
  updatedAt     DateTime  @updatedAt       @map("updated_at")

  apiKeys       ApiKey[]

  @@map("users")
  @@index([email])
}
```

Add `mustChangePassword` right after `isActive`:

```prisma
model User {
  id                 String    @id @default(cuid())
  email              String    @unique
  passwordHash       String?   @map("password_hash")
  displayName        String?   @map("display_name")
  role               UserRole  @default(member)
  isActive           Boolean   @default(true) @map("is_active")
  mustChangePassword Boolean   @default(false) @map("must_change_password")
  createdAt          DateTime  @default(now()) @map("created_at")
  updatedAt          DateTime  @updatedAt       @map("updated_at")

  apiKeys            ApiKey[]

  @@map("users")
  @@index([email])
}
```

- [ ] **Step 2: Run the migration**

Run: `uv run prisma migrate dev --name add_must_change_password`
Expected: migration file created under `prisma/migrations/`, applies cleanly against the dev Neon DB, `prisma generate` re-runs automatically.

- [ ] **Step 3: Commit**

```bash
git add prisma/schema.prisma prisma/migrations
git commit -m "$(cat <<'EOF'
feat(db): add User.mustChangePassword for forced password resets

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A2: Config — new settings

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Add `jwt_password_change_ttl_seconds`**

Find `jwt_refresh_ttl_seconds: int = 2592000  # 30 days` (around line 47) and add directly below it:

```python
    jwt_refresh_ttl_seconds: int = 2592000  # 30 days
    jwt_password_change_ttl_seconds: int = 600  # 10 minutes — forced-reset completion window
```

- [ ] **Step 2: Add `rate_limit_change_password_per_minute`**

Find the rate-limit block (around line 135-137):

```python
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_minute: int = 5
    rate_limit_refresh_per_minute: int = 30
```

Add a fourth line:

```python
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_minute: int = 5
    rate_limit_refresh_per_minute: int = 30
    rate_limit_change_password_per_minute: int = 10
```

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "$(cat <<'EOF'
feat(config): add password-change token TTL and rate limit settings

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A3: `password_change` JWT type

**Files:**
- Modify: `src/security/jwt.py`
- Test: `tests/unit/security/test_jwt.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/security/test_jwt.py`:

```python
"""Tests for the password_change JWT type (Account + Workflow Sharing plan, Part A)."""

import time

import pytest

from src.security.jwt import (
    TokenVerificationError,
    create_access_token,
    create_password_change_token,
    verify_password_change_token,
)


def test_create_and_verify_password_change_token() -> None:
    token = create_password_change_token("u1")
    payload = verify_password_change_token(token)
    assert payload.sub == "u1"
    assert payload.type == "password_change"


def test_password_change_token_rejects_access_token() -> None:
    access = create_access_token("u1")
    with pytest.raises(TokenVerificationError):
        verify_password_change_token(access)


def test_password_change_token_respects_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("JWT_PASSWORD_CHANGE_TTL_SECONDS", "1")
    get_settings.cache_clear()
    token = create_password_change_token("u1")
    time.sleep(1.2)
    with pytest.raises(TokenVerificationError):
        verify_password_change_token(token)
    get_settings.cache_clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/security/test_jwt.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_password_change_token'`

- [ ] **Step 3: Implement**

In `src/security/jwt.py`, add a third payload class after `RefreshTokenPayload` (around line 30):

```python
class PasswordChangeTokenPayload(BaseModel):
    sub: str
    iat: int
    exp: int
    type: str = "password_change"
```

Add `create_password_change_token` and `verify_password_change_token` after `create_refresh_token` / `verify_refresh_token` respectively:

```python
def create_password_change_token(user_id: str) -> str:
    settings = get_settings()
    now = _now()
    payload = PasswordChangeTokenPayload(
        sub=user_id,
        iat=now,
        exp=now + settings.jwt_password_change_ttl_seconds,
    )
    return _encode(payload)
```

```python
def verify_password_change_token(token: str) -> PasswordChangeTokenPayload:
    raw = _decode(token)
    if raw.get("type") != "password_change":
        raise TokenVerificationError(
            f"Expected token type 'password_change', got {raw.get('type')!r}"
        )
    return PasswordChangeTokenPayload.model_validate(raw)
```

Update `__all__` to include the three new names:

```python
__all__ = [
    "AccessTokenPayload",
    "PasswordChangeTokenPayload",
    "RefreshTokenPayload",
    "TokenExpiredError",
    "TokenVerificationError",
    "create_access_token",
    "create_password_change_token",
    "create_refresh_token",
    "verify_access_token",
    "verify_password_change_token",
    "verify_refresh_token",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/security/test_jwt.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/security/jwt.py tests/unit/security/test_jwt.py
git commit -m "$(cat <<'EOF'
feat(security): add password_change JWT type

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A4: Auth dependency accepting access OR password_change tokens

**Files:**
- Modify: `src/security/auth.py`
- Test: `tests/unit/security/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/security/test_auth.py`:

```python
def test_get_user_id_allow_password_change_accepts_access_token() -> None:
    import asyncio

    from src.security.auth import get_user_id_allow_password_change
    from src.security.jwt import create_access_token

    token = create_access_token("u1")
    req = _mock_request(f"Bearer {token}")
    user_id = asyncio.get_event_loop().run_until_complete(
        get_user_id_allow_password_change(req)
    )
    assert user_id == "u1"


def test_get_user_id_allow_password_change_accepts_password_change_token() -> None:
    import asyncio

    from src.security.auth import get_user_id_allow_password_change
    from src.security.jwt import create_password_change_token

    token = create_password_change_token("u1")
    req = _mock_request(f"Bearer {token}")
    user_id = asyncio.get_event_loop().run_until_complete(
        get_user_id_allow_password_change(req)
    )
    assert user_id == "u1"


def test_get_user_id_allow_password_change_rejects_refresh_token() -> None:
    import asyncio

    from src.security.auth import AuthError, get_user_id_allow_password_change
    from src.security.jwt import create_refresh_token

    token = create_refresh_token("u1")
    req = _mock_request(f"Bearer {token}")
    with pytest.raises(AuthError):
        asyncio.get_event_loop().run_until_complete(
            get_user_id_allow_password_change(req)
        )


def test_get_user_id_allow_password_change_rejects_missing_header() -> None:
    import asyncio

    from src.security.auth import AuthError, get_user_id_allow_password_change

    req = _mock_request(None)
    with pytest.raises(AuthError):
        asyncio.get_event_loop().run_until_complete(
            get_user_id_allow_password_change(req)
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/security/test_auth.py -v -k allow_password_change`
Expected: FAIL with `ImportError: cannot import name 'get_user_id_allow_password_change'`

- [ ] **Step 3: Implement**

In `src/security/auth.py`, add the import at the top (extend the existing `from src.config import Settings, get_settings` block is unrelated — add a new import line for the jwt helpers):

```python
from src.security.jwt import (
    TokenVerificationError,
    verify_access_token,
    verify_password_change_token,
)
```

Add the new dependency after `get_current_user_id` (after line 162, before `get_current_role`):

```python
async def get_user_id_allow_password_change(request: Request) -> str:
    """Standalone-mode only. Accepts EITHER a normal access token (self-service
    change while already logged in) OR a password_change token (completing an
    admin-forced reset). Raises AuthError (401) for anything else — including
    refresh tokens, which must never authorize this endpoint.
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthError("missing Authorization header")
    try:
        return verify_access_token(token).sub
    except TokenVerificationError:
        pass
    try:
        return verify_password_change_token(token).sub
    except TokenVerificationError as exc:
        raise AuthError(f"invalid token: {exc}") from exc
```

Add `"get_user_id_allow_password_change"` to `__all__` (alphabetical, after `ensure_admin`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/security/test_auth.py -v -k allow_password_change`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/security/auth.py tests/unit/security/test_auth.py
git commit -m "$(cat <<'EOF'
feat(security): add auth dependency accepting access or password_change tokens

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A5: Login enforcement + `/auth/change-password` endpoint

**Files:**
- Modify: `src/api/auth_standalone.py`
- Test: `tests/unit/api/test_auth_standalone.py`
- Test: `tests/unit/api/test_auth_change_password.py` (new)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/api/test_auth_standalone.py`:

```python
def test_login_must_change_password_returns_restricted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client_standalone(monkeypatch)
    from src.security.passwords import hash_password

    db.user.find_unique = AsyncMock(
        return_value=_user_row(
            passwordHash=hash_password("temp-pass-123"), mustChangePassword=True
        )
    )
    resp = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "temp-pass-123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mustChangePassword"] is True
    assert "passwordChangeToken" in body
    assert "accessToken" not in body
```

Create `tests/unit/api/test_auth_change_password.py`:

```python
"""Tests for POST /auth/change-password (Account + Workflow Sharing plan, Part A)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.security.rate_limit import RateLimiter


def _user_row(**overrides: Any) -> SimpleNamespace:
    from src.security.passwords import hash_password

    base: dict[str, Any] = {
        "id": "u1",
        "email": "alice@example.com",
        "passwordHash": hash_password("old-password-8"),
        "displayName": "Alice",
        "role": "member",
        "isActive": True,
        "mustChangePassword": False,
        "createdAt": "2026-04-21T00:00:00Z",
        "updatedAt": "2026-04-21T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    from fastapi import FastAPI

    from src.api.auth_standalone import router as auth_router

    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(auth_router)
    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=_user_row())
    db.user.update = AsyncMock(return_value=_user_row(passwordHash="$2b$new"))
    app.state.db = db
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_change_password_with_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_access_token

    client, db = _client(monkeypatch)
    token = create_access_token("u1")
    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": "old-password-8", "newPassword": "new-password-99"},
    )
    assert resp.status_code == 204, resp.text
    db.user.update.assert_awaited_once()
    data = db.user.update.await_args.kwargs["data"]
    assert data["mustChangePassword"] is False
    assert data["passwordHash"] != "old-password-8"


def test_change_password_with_password_change_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.jwt import create_password_change_token

    client, db = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": "old-password-8", "newPassword": "new-password-99"},
    )
    assert resp.status_code == 204, resp.text


def test_change_password_rejects_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_refresh_token

    client, _ = _client(monkeypatch)
    token = create_refresh_token("u1")
    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": "old-password-8", "newPassword": "new-password-99"},
    )
    assert resp.status_code == 401


def test_change_password_wrong_current_password_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.jwt import create_access_token

    client, _ = _client(monkeypatch)
    token = create_access_token("u1")
    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": "wrong-password", "newPassword": "new-password-99"},
    )
    assert resp.status_code == 401


def test_change_password_short_new_password_422(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_access_token

    client, _ = _client(monkeypatch)
    token = create_access_token("u1")
    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": "old-password-8", "newPassword": "short"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_auth_change_password.py tests/unit/api/test_auth_standalone.py -v -k "change_password or must_change_password"`
Expected: FAIL — `/auth/change-password` 404s, and login test fails because `mustChangePassword` isn't checked yet.

- [ ] **Step 3: Implement**

In `src/api/auth_standalone.py`, update the imports:

```python
from src.security.auth import get_user_id_allow_password_change
from src.security.jwt import (
    TokenVerificationError,
    create_access_token,
    create_password_change_token,
    create_refresh_token,
    verify_refresh_token,
)
```

Add a new response model after `TokenPairResponse` (around line 71):

```python
class MustChangePasswordResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    must_change_password: bool = Field(default=True, alias="mustChangePassword")
    password_change_token: str = Field(alias="passwordChangeToken")


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_password: str = Field(alias="currentPassword")
    new_password: str = Field(min_length=8, alias="newPassword")
```

Change the `login` route's decorator and signature to allow the union response, and add the `mustChangePassword` branch right before token issuance:

```python
@router.post("/auth/login", response_model=TokenPairResponse | MustChangePasswordResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> TokenPairResponse | MustChangePasswordResponse:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_login",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_login_per_minute),
    )
    user = await db.user.find_unique(where={"email": str(payload.email)})  # pyright: ignore[reportAttributeAccessIssue]
    if (
        user is None
        or user.passwordHash is None
        or not verify_password(payload.password, user.passwordHash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        )
    if getattr(user, "isActive", True) is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account is deactivated")
    if getattr(user, "mustChangePassword", False):
        return MustChangePasswordResponse(
            passwordChangeToken=create_password_change_token(user.id)
        )
    access, refresh, access_exp, refresh_exp = _issue_pair(user.id)
    return TokenPairResponse(
        accessToken=access,
        refreshToken=refresh,
        accessTokenExpiresAt=access_exp,
        refreshTokenExpiresAt=refresh_exp,
    )
```

Add the new endpoint after `disconnect` (before `__all__`):

```python
@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_user_id_allow_password_change),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_change_password",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_change_password_per_minute),
    )
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if (
        user is None
        or user.passwordHash is None
        or not verify_password(payload.current_password, user.passwordHash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="current password is incorrect"
        )
    await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id},
        data={
            "passwordHash": hash_password(payload.new_password),
            "mustChangePassword": False,
        },
    )
    return None
```

Update `__all__` to include `"ChangePasswordRequest"` and `"MustChangePasswordResponse"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_auth_change_password.py tests/unit/api/test_auth_standalone.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/api/auth_standalone.py tests/unit/api/test_auth_change_password.py tests/unit/api/test_auth_standalone.py
git commit -m "$(cat <<'EOF'
feat(auth): add self-service change-password + forced-reset login gate

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A6: Admin-forced reset endpoint

**Files:**
- Modify: `src/api/admin_users.py`
- Test: `tests/unit/api/test_admin_users.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_admin_users.py`:

```python
"""Tests for /admin/users/* (Account + Workflow Sharing plan, Part A adds reset-password)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.admin_users import router as admin_users_router
from src.security.jwt import create_access_token


def _user_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "u1",
        "email": "alice@example.com",
        "role": "member",
        "displayName": "Alice",
        "isActive": True,
        "mustChangePassword": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _admin_row(**overrides: Any) -> SimpleNamespace:
    return _user_row(id="admin1", email="admin@example.com", role="admin", **overrides)


def _client(monkeypatch: pytest.MonkeyPatch, target_user: SimpleNamespace) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(admin_users_router)
    db = MagicMock()
    db.user = MagicMock()

    async def _find_unique(where: dict[str, Any]) -> SimpleNamespace | None:
        if where.get("id") == "admin1":
            return _admin_row()
        if where.get("id") == target_user.id:
            return target_user
        return None

    db.user.find_unique = AsyncMock(side_effect=_find_unique)
    db.user.update = AsyncMock(
        return_value=_user_row(id=target_user.id, mustChangePassword=True)
    )
    app.state.db = db
    return TestClient(app)


def _admin_token() -> str:
    return create_access_token("admin1")


def test_admin_reset_password_returns_temp_password_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _user_row()
    client = _client(monkeypatch, target)
    resp = client.post(
        f"/admin/users/{target.id}/reset-password",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "temporaryPassword" in body
    assert len(body["temporaryPassword"]) >= 12


def test_admin_reset_password_sets_must_change_password_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _user_row()
    client = _client(monkeypatch, target)
    client.post(
        f"/admin/users/{target.id}/reset-password",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    db = client.app.state.db  # type: ignore[attr-defined]
    data = db.user.update.await_args.kwargs["data"]
    assert data["mustChangePassword"] is True
    assert "passwordHash" in data


def test_admin_reset_password_unknown_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _user_row()
    client = _client(monkeypatch, target)
    resp = client.post(
        "/admin/users/ghost/reset-password",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert resp.status_code == 404


def test_admin_reset_password_requires_admin_role(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _user_row()
    client = _client(monkeypatch, target)
    member_token = create_access_token(target.id)
    resp = client.post(
        f"/admin/users/{target.id}/reset-password",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_admin_users.py -v`
Expected: FAIL with 404 (route doesn't exist)

- [ ] **Step 3: Implement**

In `src/api/admin_users.py`, add `secrets` to the imports and pull in the password hasher:

```python
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.security.auth import ensure_admin
from src.security.passwords import hash_password
from src.storage.db import get_db
```

Add a response model near `RoleUpdateRequest`:

```python
class ResetPasswordResponse(BaseModel):
    temporary_password: str = Field(alias="temporaryPassword")

    class Config:
        populate_by_name = True
```

Add the endpoint after `admin_revoke_api_key` (before `__all__`):

```python
@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def admin_reset_password(
    user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> ResetPasswordResponse:  # pyright: ignore[reportUnusedFunction]
    """Admin-forced reset: generate a temp password, force a change on next
    login. The plaintext temp password is returned exactly once — it is
    never stored or logged in plaintext.
    """
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    temp_password = secrets.token_urlsafe(12)
    await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id},
        data={
            "passwordHash": hash_password(temp_password),
            "mustChangePassword": True,
        },
    )
    return ResetPasswordResponse(temporaryPassword=temp_password)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_admin_users.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/api/admin_users.py tests/unit/api/test_admin_users.py
git commit -m "$(cat <<'EOF'
feat(admin): add admin-forced password reset endpoint

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A7: Regenerate frontend OpenAPI client

**Files:**
- Modify: `frontend/lib/api/generated/schema.ts` (generated, not hand-edited)

- [ ] **Step 1: Start the backend locally**

Run: `uv run uvicorn src.main:app --reload` (leave running in a separate terminal)

- [ ] **Step 2: Regenerate**

Run (from `frontend/`): `npm run generate-client`
Expected: `lib/api/generated/schema.ts` updates to include `MustChangePasswordResponse`, `ChangePasswordRequest`, `ResetPasswordResponse`, and the new `/auth/change-password`, `/admin/users/{user_id}/reset-password` paths.

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/api/generated/schema.ts
git commit -m "$(cat <<'EOF'
chore(frontend): regenerate OpenAPI client for password-reset endpoints

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A8: `composer-api.ts` — login union type + `composerChangePassword`

**Files:**
- Modify: `frontend/lib/composer-api.ts`
- Test: `frontend/lib/auth.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/lib/auth.test.ts` (inside the existing `describe("composer session helpers", ...)` block, add these `it`s):

```ts
  it("composerLogin returns mustChangePassword variant without normalizing", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        mustChangePassword: true,
        passwordChangeToken: "pc-token-123",
      }),
    });
    const { composerLogin } = await import("@/lib/composer-api");
    const result = await composerLogin("u@example.com", "temp-pass");
    expect(result).toEqual({
      mustChangePassword: true,
      passwordChangeToken: "pc-token-123",
    });
  });

  it("composerChangePassword posts with bearer token and resolves on 204", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204 });
    const { composerChangePassword } = await import("@/lib/composer-api");
    await composerChangePassword("tok-1", "old-pw", "new-pw");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/change-password"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer tok-1" }),
      })
    );
  });

  it("composerChangePassword throws on non-OK response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });
    const { composerChangePassword } = await import("@/lib/composer-api");
    await expect(
      composerChangePassword("tok-1", "wrong", "new-pw")
    ).rejects.toThrow(/change password failed/);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- lib/auth.test.ts`
Expected: FAIL — `composerChangePassword` is not exported, and `composerLogin`'s current implementation calls `normalize()` unconditionally which throws on the shape used in the first new test.

- [ ] **Step 3: Implement**

In `frontend/lib/composer-api.ts`, add a new type after `ComposerTokens`:

```ts
export type ComposerMustChangePassword = {
  mustChangePassword: true;
  passwordChangeToken: string;
};
```

Replace `composerLogin`:

```ts
export async function composerLogin(
  email: string,
  password: string
): Promise<ComposerTokens | ComposerMustChangePassword> {
  const res = await fetch(`${composerApiUrl}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(`login failed: ${res.status}`);
  const body = (await res.json()) as
    | TokenPairWire
    | ComposerMustChangePassword;
  if ("mustChangePassword" in body && body.mustChangePassword) {
    return body;
  }
  return normalize(body as TokenPairWire);
}
```

Add `composerChangePassword` after `composerRefresh`:

```ts
export async function composerChangePassword(
  token: string,
  currentPassword: string,
  newPassword: string
): Promise<void> {
  const res = await fetch(`${composerApiUrl}/auth/change-password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ currentPassword, newPassword }),
  });
  if (!res.ok) throw new Error(`change password failed: ${res.status}`);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- lib/auth.test.ts`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/composer-api.ts frontend/lib/auth.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): add composerChangePassword + mustChangePassword login variant

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A9: NextAuth wiring for the forced-reset session

**Files:**
- Modify: `frontend/auth.ts`
- Modify: `frontend/types/next-auth.d.ts`

- [ ] **Step 1: Extend the type declarations**

In `frontend/types/next-auth.d.ts`, add to `interface Session`:

```ts
    role?: "admin" | "member";
    /** True when the account has a pending admin-forced password reset.
     *  A session with this set carries no usable Composer accessToken. */
    mustChangePassword?: boolean;
    /** Short-lived token that authorizes ONLY /auth/change-password.
     *  Present only when mustChangePassword is true. */
    passwordChangeToken?: string;
```

And to `interface JWT` (in the `next-auth/jwt` module block):

```ts
    role?: "admin" | "member";
    mustChangePassword?: boolean;
    passwordChangeToken?: string;
```

- [ ] **Step 2: Update `ComposerJwt` and `authorize()`**

In `frontend/auth.ts`, extend `ComposerJwt`:

```ts
type ComposerJwt = JWT & {
  composerAccessToken?: string;
  composerRefreshToken?: string;
  accessTokenExpiresAt?: number;
  refreshTokenExpiresAt?: number;
  role?: "admin" | "member";
  mustChangePassword?: boolean;
  passwordChangeToken?: string;
  error?: "RefreshAccessTokenError";
};
```

Replace the `Credentials({...})` provider's `authorize`:

```ts
      async authorize(creds) {
        if (!creds?.email || !creds?.password) return null;
        try {
          const result = await composerLogin(
            creds.email as string,
            creds.password as string
          );
          if ("mustChangePassword" in result && result.mustChangePassword) {
            return {
              id: creds.email as string,
              email: creds.email as string,
              mustChangePassword: true,
              passwordChangeToken: result.passwordChangeToken,
            } as never;
          }
          const profile = await composerMe(result.accessToken);
          return {
            id: profile.id,
            email: profile.email,
            name: profile.displayName,
            composerAccessToken: result.accessToken,
            composerRefreshToken: result.refreshToken,
            accessTokenExpiresAt: result.accessTokenExpiresAt,
            refreshTokenExpiresAt: result.refreshTokenExpiresAt,
            role: profile.role,
          } as never;
        } catch {
          return null;
        }
      },
```

- [ ] **Step 3: Update the `jwt()` callback's first-sign-in branch**

Replace the `if (user) { ... }` block in the `jwt` callback:

```ts
      if (user) {
        const u = user as unknown as {
          composerAccessToken?: string;
          composerRefreshToken?: string;
          accessTokenExpiresAt?: number;
          refreshTokenExpiresAt?: number;
          role?: "admin" | "member";
          mustChangePassword?: boolean;
          passwordChangeToken?: string;
        };
        if (u.mustChangePassword) {
          t.mustChangePassword = true;
          t.passwordChangeToken = u.passwordChangeToken;
          t.error = undefined;
          return t;
        }
        if (u.composerAccessToken) {
          t.composerAccessToken = u.composerAccessToken;
          t.composerRefreshToken = u.composerRefreshToken;
          t.accessTokenExpiresAt = u.accessTokenExpiresAt;
          t.refreshTokenExpiresAt = u.refreshTokenExpiresAt;
          t.role = u.role;
          t.error = undefined;
          return t;
        }
      }
```

- [ ] **Step 4: Update the `session()` callback**

Replace the `session` callback body:

```ts
    async session({ session, token }) {
      const t = token as ComposerJwt;
      const s = session as unknown as {
        accessToken?: string;
        accessTokenExpiresAt?: number;
        refreshTokenExpiresAt?: number;
        role?: "admin" | "member";
        mustChangePassword?: boolean;
        passwordChangeToken?: string;
        error?: "RefreshAccessTokenError";
        user?: { id?: string };
      };
      s.accessToken = t.composerAccessToken;
      s.accessTokenExpiresAt = t.accessTokenExpiresAt;
      s.refreshTokenExpiresAt = t.refreshTokenExpiresAt;
      s.role = t.role;
      s.mustChangePassword = t.mustChangePassword;
      s.passwordChangeToken = t.passwordChangeToken;
      s.error = t.error;
      if (t.sub) s.user = { ...s.user, id: t.sub };
      return session;
    },
```

- [ ] **Step 5: Type-check**

Run (from `frontend/`): `npm run type-check`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add frontend/auth.ts frontend/types/next-auth.d.ts
git commit -m "$(cat <<'EOF'
feat(frontend): thread mustChangePassword through NextAuth session

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A10: Login page redirect + `/change-password` page

**Files:**
- Modify: `frontend/app/(auth)/login/page.tsx`
- Create: `frontend/app/(auth)/change-password/page.tsx`
- Modify: `frontend/components/composer/app-shell.tsx`

- [ ] **Step 1: Update the login page**

In `frontend/app/(auth)/login/page.tsx`, change the import line:

```tsx
import { signIn, getSession } from "next-auth/react";
```

Replace `onCredentialsSubmit`:

```tsx
  async function onCredentialsSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    const res = await signIn("credentials", {
      email,
      password,
      redirect: false,
    });
    if (res?.error) {
      setSubmitting(false);
      toast.error("Sign-in failed. Check your credentials.");
      return;
    }
    const session = await getSession();
    setSubmitting(false);
    if ((session as { mustChangePassword?: boolean } | null)?.mustChangePassword) {
      router.push("/change-password");
      return;
    }
    router.push(returnTo);
  }
```

- [ ] **Step 2: Create the change-password page**

Create `frontend/app/(auth)/change-password/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useSession, signOut } from "next-auth/react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ComposerLogo } from "@/components/composer/composer-logo";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { composerChangePassword } from "@/lib/composer-api";

export default function ChangePasswordPage() {
  const { data: session } = useSession();
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const sessionTokens = session as {
    passwordChangeToken?: string;
    accessToken?: string;
    mustChangePassword?: boolean;
  } | null;
  const token = sessionTokens?.passwordChangeToken ?? sessionTokens?.accessToken;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) {
      toast.error("Your session has expired. Please sign in again.");
      router.push("/login");
      return;
    }
    setSubmitting(true);
    try {
      await composerChangePassword(token, currentPassword, newPassword);
      toast.success("Password changed. Please sign in again.");
      await signOut({ redirect: false });
      router.push("/login");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to change password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="w-full max-w-md shadow-lg">
      <CardHeader className="items-center text-center">
        <ComposerLogo size={48} className="mb-3 text-primary" />
        <CardTitle className="text-2xl">Change your password</CardTitle>
        {sessionTokens?.mustChangePassword && (
          <CardDescription>
            An admin reset your password. Choose a new one to continue.
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="current-password">Current password</Label>
            <Input
              id="current-password"
              type="password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-password">New password</Label>
            <Input
              id="new-password"
              type="password"
              required
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? "Changing…" : "Change password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Add a self-service entry point**

In `frontend/components/composer/app-shell.tsx`, add a link in the sidebar footer, right after the email `div` (around line 42):

```tsx
        <div className="border-t border-white/10 px-5 py-4 text-xs">
          <div className="truncate font-semibold text-white/65">
            {session?.user?.email}
          </div>
          <Link
            href="/change-password"
            className="mt-1 block text-[0.7rem] text-white/40 hover:text-white/90"
          >
            Change password
          </Link>
          <button
            type="button"
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="mt-1 text-[0.7rem] text-white/40 hover:text-white/90"
          >
            Sign out
          </button>
        </div>
```

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npm run type-check`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/(auth)/login/page.tsx" "frontend/app/(auth)/change-password/page.tsx" frontend/components/composer/app-shell.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): add change-password page + forced-reset redirect

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A11: Admin "Reset password" dialog

**Files:**
- Modify: `frontend/lib/api/admin.ts`
- Create: `frontend/components/composer/reset-password-dialog.tsx`
- Modify: `frontend/app/admin/users/page.tsx`

- [ ] **Step 1: Add the API client function**

In `frontend/lib/api/admin.ts`, add after `reactivateUser`:

```ts
export async function resetUserPassword(id: string): Promise<{ temporaryPassword: string }> {
  return apiFetch<{ temporaryPassword: string }>(`/admin/users/${id}/reset-password`, {
    method: "POST",
  });
}
```

- [ ] **Step 2: Create the dialog component**

Create `frontend/components/composer/reset-password-dialog.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { type AdminUser, resetUserPassword } from "@/lib/api/admin";

export function ResetPasswordDialog({ user }: { user: AdminUser }) {
  const [open, setOpen] = useState(false);
  const [tempPassword, setTempPassword] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => resetUserPassword(user.id),
    onSuccess: ({ temporaryPassword }) => {
      setTempPassword(temporaryPassword);
      toast.success(`Password reset for ${user.email}.`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  function close() {
    setOpen(false);
    setTempPassword(null);
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Reset password
      </Button>
      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset password for {user.email}</DialogTitle>
            <DialogDescription>
              {tempPassword
                ? "Share this temporary password with the user. It will not be shown again — they must change it on next login."
                : "Generates a random temporary password and forces the user to change it on their next sign-in."}
            </DialogDescription>
          </DialogHeader>
          {tempPassword && (
            <div className="rounded-md border bg-muted/40 p-3">
              <code className="break-all text-sm font-medium">{tempPassword}</code>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              {tempPassword ? "Close" : "Cancel"}
            </Button>
            {!tempPassword && (
              <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
                {mutation.isPending ? "Resetting…" : "Reset password"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

- [ ] **Step 3: Wire into the admin users page**

In `frontend/app/admin/users/page.tsx`, add the import:

```tsx
import { ResetPasswordDialog } from "@/components/composer/reset-password-dialog";
```

Add the component to the actions cell:

```tsx
                  <TableCell className="flex items-center justify-end gap-2">
                    <UserRoleToggle user={user} />
                    <UserActiveToggle user={user} />
                    <ResetPasswordDialog user={user} />
                  </TableCell>
```

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npm run type-check`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/admin.ts frontend/components/composer/reset-password-dialog.tsx frontend/app/admin/users/page.tsx
git commit -m "$(cat <<'EOF'
feat(admin): add reset-password dialog to the admin users page

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task A12: ADR-0024

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1: Append the ADR**

Add to the end of `docs/decisions.md`:

```markdown
---

## ADR-0024: Password reset — admin-only, third JWT type gates change-password

**Status:** Accepted (2026-07-09)

**Context.** Composer had no way for a user to recover a forgotten password, nor for an admin to reset one. The user explicitly chose an admin-only reset (no email dependency) over a self-service emailed-link flow. Implementing the "must change password" gate needed a way to issue a session that can do exactly one thing (call `/auth/change-password`) without granting normal API access.

**Decision.** Add a third JWT `type` discriminator, `password_change`, alongside the existing `access`/`refresh` types in `src/security/jwt.py`. Every existing route dependency (`get_current_user_id`) already rejects any token whose `type` isn't `"access"`, so a `password_change` token is automatically unusable everywhere except the one new dependency (`get_user_id_allow_password_change`) that explicitly accepts it. `POST /auth/login` returns this restricted token instead of a normal pair when `User.mustChangePassword` is true; `POST /admin/users/{id}/reset-password` sets that flag after generating a random temp password. `POST /auth/change-password` accepts either an access token (voluntary self-service) or a password_change token (completing a forced reset) and always re-verifies the caller's current password before accepting a new one.

**Consequences.**
- No new route-level authorization logic needed anywhere else — the type discriminator does the restriction for free.
- Admins never see or set a user's real password (only a randomly generated temp one), closing an "admin knows my password" gap that a naive "admin sets exact password" design would have had.
- Email-based self-service reset remains out of scope; if added later, it can reuse the same `password_change` token type.

**Implemented by.** `docs/archive/phase-history/plans/2026-07-09-account-workflow-sharing-plan.md`, Part A.

**Related.** ADR-0005 (Phase 1 API surface + authentication), ADR-0015 (dev-mode auth fallback).
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions.md
git commit -m "$(cat <<'EOF'
docs: add ADR-0024 for the password-reset design

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Part B — Many-to-many workflow assignment

### Task B1: Schema — `WorkflowAssignment`

**Files:**
- Modify: `prisma/schema.prisma`

- [ ] **Step 1: Add the relation field to `Workflow`**

In `prisma/schema.prisma`, the `Workflow` model's relation block currently reads:

```prisma
  executions    WorkflowExecution[]

  @@map("workflows")
```

Add the new back-reference:

```prisma
  executions    WorkflowExecution[]
  assignments   WorkflowAssignment[]

  @@map("workflows")
```

- [ ] **Step 2: Add the `WorkflowAssignment` model**

Add after the `WorkflowExecution` model (after its closing `}` around line 72):

```prisma
model WorkflowAssignment {
  id           String   @id @default(cuid())
  workflowId   String   @map("workflow_id")
  userId       String   @map("user_id")
  assignedById String   @map("assigned_by_id")
  assignedAt   DateTime @default(now()) @map("assigned_at")

  workflow     Workflow @relation(fields: [workflowId], references: [id], onDelete: Cascade)

  @@unique([workflowId, userId])
  @@map("workflow_assignments")
  @@index([workflowId])
  @@index([userId])
}
```

- [ ] **Step 3: Migrate**

Run: `uv run prisma migrate dev --name add_workflow_assignment`
Expected: migration applies cleanly; `prisma generate` re-runs.

- [ ] **Step 4: Commit**

```bash
git add prisma/schema.prisma prisma/migrations
git commit -m "$(cat <<'EOF'
feat(db): add WorkflowAssignment join table for many-to-many sharing

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task B2: `GET /users/search` (non-admin user lookup)

**Files:**
- Create: `src/api/users.py`
- Modify: `src/main.py` (register the router)
- Test: `tests/unit/api/test_users_search.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_users_search.py`:

```python
"""Tests for GET /users/search (Account + Workflow Sharing plan, Part B)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.users import router as users_router
from src.security.jwt import create_access_token


def _user_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "u2",
        "email": "bob@example.com",
        "displayName": "Bob",
        "isActive": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace]) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(users_router)
    db = MagicMock()
    db.user = MagicMock()
    db.user.find_many = AsyncMock(return_value=rows)
    app.state.db = db
    return TestClient(app)


def test_search_users_returns_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_user_row()])
    token = create_access_token("u1")
    resp = client.get(
        "/users/search?q=bob", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["email"] == "bob@example.com"
    assert "isActive" not in body[0]


def test_search_users_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [])
    resp = client.get("/users/search?q=bob")
    assert resp.status_code == 401


def test_search_users_requires_min_query_length(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [])
    token = create_access_token("u1")
    resp = client.get(
        "/users/search?q=b", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_users_search.py -v`
Expected: FAIL — `src.api.users` module doesn't exist yet

- [ ] **Step 3: Implement**

Create `src/api/users.py`:

```python
"""GET /users/search — minimal user lookup for any authenticated user.

Unlike /admin/users (admin-only, full user records), this endpoint lets a
non-admin workflow owner find people to share a workflow with. Deliberately
returns only id/email/displayName — no role, no isActive, no other admin-only
fields — to avoid turning this into a general user-enumeration endpoint.
"""

from pydantic import BaseModel, ConfigDict, Field

from fastapi import APIRouter, Depends, Query

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.security.auth import get_current_user_id
from src.storage.db import get_db

router = APIRouter(tags=["users"])


class UserSearchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    email: str
    display_name: str | None = Field(default=None, alias="displayName")


@router.get("/users/search", response_model=list[UserSearchResult])
async def search_users(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=20, ge=1, le=20),
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _user_id: str = Depends(get_current_user_id),
) -> list[UserSearchResult]:  # pyright: ignore[reportUnusedFunction]
    rows = await db.user.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={
            "isActive": True,
            "OR": [
                {"email": {"contains": q, "mode": "insensitive"}},
                {"displayName": {"contains": q, "mode": "insensitive"}},
            ],
        },
        take=limit,
        order={"email": "asc"},
    )
    return [UserSearchResult.model_validate(row) for row in rows]


__all__ = ["UserSearchResult", "router"]
```

Register the router in `src/main.py` — find where `workflows.router` (or similarly-shaped routers) is included and add a line for `users.router`. Search first:

Run: `grep -n "include_router" src/main.py`

Add, near the other `src.api.*` router imports and `include_router` calls:

```python
from src.api import users as users_api
```

```python
app.include_router(users_api.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_users_search.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/api/users.py src/main.py tests/unit/api/test_users_search.py
git commit -m "$(cat <<'EOF'
feat(api): add GET /users/search for non-admin workflow-sharing lookups

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task B3: Assignment CRUD + authorization relax on workflows.py

**Files:**
- Modify: `src/api/workflows.py`
- Test: `tests/unit/api/test_workflow_assignments.py` (new)
- Modify: `tests/unit/api/test_workflows_crud.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_workflow_assignments.py`:

```python
"""Tests for workflow assignment CRUD + authz relax (Account + Workflow Sharing plan, Part B)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.jwt import create_access_token


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "owner1",
        "name": "Shared workflow",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [{"id": "s", "type": "start"}],
        "edges": [],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "isProduction": False,
        "externalSlug": None,
        "createdAt": "2026-07-09T00:00:00Z",
        "updatedAt": "2026-07-09T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _assignment_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "a1",
        "workflowId": "w1",
        "userId": "assignee1",
        "assignedById": "owner1",
        "assignedAt": "2026-07-09T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflowassignment = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="assignee1", role="member", isActive=True)
    )
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_owner_can_grant_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=None)
    db.workflowassignment.create = AsyncMock(return_value=_assignment_row())
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    db.workflowassignment.create.assert_awaited_once()


def test_non_owner_non_admin_cannot_grant_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    token = create_access_token("stranger1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_owner_can_revoke_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.delete = AsyncMock(return_value=_assignment_row())
    token = create_access_token("owner1")
    resp = client.delete(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text


def test_list_assignments(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_many = AsyncMock(return_value=[_assignment_row()])
    token = create_access_token("owner1")
    resp = client.get(
        "/workflows/w1/assignments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["userId"] == "assignee1"


def test_assignee_can_read_private_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=_assignment_row())
    token = create_access_token("assignee1")
    resp = client.get("/workflows/w1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


def test_non_assignee_still_gets_404_on_private_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=None)
    token = create_access_token("stranger1")
    resp = client.get("/workflows/w1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_assignee_can_update_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=_assignment_row())
    db.workflow.update = AsyncMock(return_value=_wf_row(name="Renamed"))
    token = create_access_token("assignee1")
    body = {
        "name": "Renamed",
        "nodes": [{"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {}}],
        "edges": [],
    }
    resp = client.put(
        "/workflows/w1", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_workflow_assignments.py -v`
Expected: FAIL — 404s on the new `/assignments` routes; assignee-authz tests fail with 404/403 from the unmodified owner-only checks.

- [ ] **Step 3: Implement**

In `src/api/workflows.py`, add a helper right after `_check_workflow_size_limits` (around line 52):

```python
async def _has_assignment(db: Prisma, workflow_id: str, user_id: str) -> bool:
    row = await db.workflowassignment.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"workflowId_userId": {"workflowId": workflow_id, "userId": user_id}}
    )
    return row is not None
```

Add request/response models near `OwnerAssignRequest` (around line 362):

```python
class WorkflowAssignmentRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    workflow_id: str = Field(alias="workflowId")
    user_id: str = Field(alias="userId")
    assigned_by_id: str = Field(alias="assignedById")
    assigned_at: Any = Field(alias="assignedAt")
```

Update `get_workflow` (around line 231-254) to also allow assignees:

```python
@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    row = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if role == "admin":
        return WorkflowRead.model_validate(row)
    if row.isPublic or row.userId == user_id:
        return WorkflowRead.model_validate(row)
    if await _has_assignment(db, workflow_id, user_id):
        return WorkflowRead.model_validate(row)
    # 404 for private, not owner, not assignee — info-leak tight (ADR-0021)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Workflow {workflow_id!r} not found.",
    )
```

Update `update_workflow`'s authz check (around line 273-277):

```python
    if role != "admin" and existing.userId != user_id:
        if not await _has_assignment(db, workflow_id, user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: not workflow owner or assignee.",
            )
```

`delete_workflow` and `assign_workflow_owner` (the single-owner transfer action) are intentionally **not** changed — deletion and ownership transfer stay owner/admin-only per the spec's "all-or-nothing access, not destructive control" scope decision.

Add the three assignment endpoints after `assign_workflow_owner` (before `__all__`):

```python
@router.get("/workflows/{workflow_id}/assignments", response_model=list[WorkflowAssignmentRead])
async def list_workflow_assignments(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> list[WorkflowAssignmentRead]:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.userId != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    rows = await db.workflowassignment.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"workflowId": workflow_id}
    )
    return [WorkflowAssignmentRead.model_validate(r) for r in rows]


@router.post(
    "/workflows/{workflow_id}/assignments/{target_user_id}",
    response_model=WorkflowAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def grant_workflow_assignment(
    workflow_id: str,
    target_user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowAssignmentRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.userId != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    target_user = await db.user.find_unique(where={"id": target_user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if target_user is None:
        raise HTTPException(404, f"user {target_user_id!r} not found")
    row = await db.workflowassignment.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "workflowId": workflow_id,
            "userId": target_user_id,
            "assignedById": user_id,
        }
    )
    return WorkflowAssignmentRead.model_validate(row)


@router.delete(
    "/workflows/{workflow_id}/assignments/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_workflow_assignment(
    workflow_id: str,
    target_user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> None:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.userId != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    await db.workflowassignment.delete(  # pyright: ignore[reportAttributeAccessIssue]
        where={"workflowId_userId": {"workflowId": workflow_id, "userId": target_user_id}}
    )
```

Update `__all__` to add `"WorkflowAssignmentRead"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_workflow_assignments.py -v`
Expected: 7 passed

- [ ] **Step 5: Regression-check existing workflow tests**

Run: `uv run pytest tests/unit/api/test_workflows_crud.py tests/unit/api/test_workflows.py -v`
Expected: all still passed (owner/admin-only paths unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/api/workflows.py tests/unit/api/test_workflow_assignments.py
git commit -m "$(cat <<'EOF'
feat(api): add workflow assignment CRUD + relax owner-only authz to assignees

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task B4: `list_workflows` — include assigned-not-owned workflows

**Files:**
- Modify: `src/api/workflows.py`
- Modify: `tests/unit/api/test_workflows_crud.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/api/test_workflows_crud.py`:

```python
def test_list_workflows_mine_includes_assigned_not_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch, [_wf_row(userId="owner-other", id="w-shared")], total=1)
    resp = client.get("/workflows?mine=true")
    assert resp.status_code == 200
    where = db.workflow.find_many.await_args.kwargs["where"]
    # mine=true must now match owner OR assignment, not just userId
    assert "OR" in where
    or_clauses = where["OR"]
    assert {"userId": "dev"} in or_clauses
    assert any("assignments" in c for c in or_clauses)


def test_list_workflows_default_view_includes_assigned_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch, [_wf_row(userId="owner-other")], total=1)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    where = db.workflow.find_many.await_args.kwargs["where"]
    or_clauses = where["OR"]
    assert any("assignments" in c for c in or_clauses)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_workflows_crud.py -v -k assigned`
Expected: FAIL — current `authz_where` for `mine=true` is `{"userId": user_id}` (no `OR`), and the default view's `OR` only has `isPublic`/`userId`.

- [ ] **Step 3: Implement**

In `list_workflows` (around line 196-202), replace the `authz_where` construction:

```python
    assignment_clause: dict[str, Any] = {"assignments": {"some": {"userId": user_id}}}
    authz_where: dict[str, Any] | None
    if mine:
        authz_where = {"OR": [{"userId": user_id}, assignment_clause]}
    elif role == "admin":
        authz_where = None
    else:
        authz_where = {
            "OR": [{"isPublic": True}, {"userId": user_id}, assignment_clause]
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_workflows_crud.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/api/workflows.py tests/unit/api/test_workflows_crud.py
git commit -m "$(cat <<'EOF'
fix(api): list_workflows includes assigned-not-owned workflows

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task B5: Regenerate frontend client + `lib/api/workflows.ts` + `lib/api/users.ts`

**Files:**
- Modify: `frontend/lib/api/generated/schema.ts` (generated)
- Modify: `frontend/lib/api/workflows.ts`
- Create: `frontend/lib/api/users.ts`

- [ ] **Step 1: Regenerate the client**

With the backend running (`uv run uvicorn src.main:app --reload`), from `frontend/`: `npm run generate-client`
Expected: schema includes `WorkflowAssignmentRead`, `UserSearchResult`, and the new `/workflows/{workflow_id}/assignments*` and `/users/search` paths.

- [ ] **Step 2: Add assignment functions to `workflows.ts`**

In `frontend/lib/api/workflows.ts`, add after `reassignWorkflowOwner`:

```ts
export type WorkflowAssignment = components["schemas"]["WorkflowAssignmentRead"];

export async function listWorkflowAssignments(
  workflowId: string
): Promise<WorkflowAssignment[]> {
  return apiFetch<WorkflowAssignment[]>(`/workflows/${workflowId}/assignments`);
}

export async function assignWorkflowUser(
  workflowId: string,
  userId: string
): Promise<WorkflowAssignment> {
  return apiFetch<WorkflowAssignment>(
    `/workflows/${workflowId}/assignments/${userId}`,
    { method: "POST" }
  );
}

export async function unassignWorkflowUser(
  workflowId: string,
  userId: string
): Promise<void> {
  return apiFetch<void>(`/workflows/${workflowId}/assignments/${userId}`, {
    method: "DELETE",
  });
}
```

- [ ] **Step 3: Create `lib/api/users.ts`**

Create `frontend/lib/api/users.ts`:

```ts
import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type UserSearchResult = components["schemas"]["UserSearchResult"];

export async function searchUsers(query: string): Promise<UserSearchResult[]> {
  if (query.trim().length < 2) return [];
  const q = new URLSearchParams({ q: query.trim() });
  return apiFetch<UserSearchResult[]>(`/users/search?${q.toString()}`);
}
```

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npm run type-check`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/generated/schema.ts frontend/lib/api/workflows.ts frontend/lib/api/users.ts
git commit -m "$(cat <<'EOF'
feat(frontend): add workflow assignment + user search API clients

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task B6: `ManageAssigneesDialog` component

**Files:**
- Create: `frontend/components/composer/manage-assignees-dialog.tsx`

- [ ] **Step 1: Implement**

Create `frontend/components/composer/manage-assignees-dialog.tsx`, modeled on `reassign-owner-dialog.tsx`'s search pattern but multi-select with a live assignee list:

```tsx
"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { searchUsers } from "@/lib/api/users";
import {
  assignWorkflowUser,
  listWorkflowAssignments,
  unassignWorkflowUser,
} from "@/lib/api/workflows";

export function ManageAssigneesDialog({
  workflowId,
  workflowName,
}: {
  workflowId: string;
  workflowName: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const qc = useQueryClient();

  const assignmentsQ = useQuery({
    queryKey: ["workflow-assignments", workflowId],
    queryFn: () => listWorkflowAssignments(workflowId),
    enabled: open,
  });

  const searchQ = useQuery({
    queryKey: ["user-search", query],
    queryFn: () => searchUsers(query),
    enabled: open && query.trim().length >= 2,
  });

  const assignedIds = useMemo(
    () => new Set((assignmentsQ.data ?? []).map((a) => a.userId)),
    [assignmentsQ.data]
  );

  const grantMutation = useMutation({
    mutationFn: (userId: string) => assignWorkflowUser(workflowId, userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workflow-assignments", workflowId] });
      toast.success("Access granted.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const revokeMutation = useMutation({
    mutationFn: (userId: string) => unassignWorkflowUser(workflowId, userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workflow-assignments", workflowId] });
      toast.success("Access revoked.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const candidates = (searchQ.data ?? []).filter((u) => !assignedIds.has(u.id));

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Manage access
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Manage access to &quot;{workflowName}&quot;</DialogTitle>
            <DialogDescription>
              Anyone granted access can open, run, and edit this workflow.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label>Currently has access</Label>
            <div className="max-h-40 overflow-y-auto rounded-md border">
              {assignmentsQ.isLoading ? (
                <div className="p-3 text-sm text-muted-foreground">Loading…</div>
              ) : (assignmentsQ.data ?? []).length === 0 ? (
                <div className="p-3 text-sm text-muted-foreground">
                  No one else has access yet.
                </div>
              ) : (
                <ul className="divide-y">
                  {(assignmentsQ.data ?? []).map((a) => (
                    <li
                      key={a.userId}
                      className="flex items-center justify-between px-3 py-2 text-sm"
                    >
                      <code className="text-xs">{a.userId}</code>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Revoke access"
                        onClick={() => revokeMutation.mutate(a.userId)}
                        disabled={revokeMutation.isPending}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="assignee-search">Grant access</Label>
            <Input
              id="assignee-search"
              placeholder="Search by email or name…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoComplete="off"
            />
            <div className="max-h-40 overflow-y-auto rounded-md border">
              {query.trim().length < 2 ? (
                <div className="p-3 text-sm text-muted-foreground">
                  Type at least 2 characters to search.
                </div>
              ) : searchQ.isLoading ? (
                <div className="p-3 text-sm text-muted-foreground">Searching…</div>
              ) : candidates.length === 0 ? (
                <div className="p-3 text-sm text-muted-foreground">No match.</div>
              ) : (
                <ul className="divide-y">
                  {candidates.map((u) => (
                    <li key={u.id} className="flex items-center justify-between px-3 py-2">
                      <div>
                        <div className="text-sm font-medium">{u.email}</div>
                        {u.displayName && (
                          <div className="text-xs text-muted-foreground">
                            {u.displayName}
                          </div>
                        )}
                      </div>
                      <Button
                        size="sm"
                        onClick={() => grantMutation.mutate(u.id)}
                        disabled={grantMutation.isPending}
                      >
                        Add
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

- [ ] **Step 2: Type-check**

Run (from `frontend/`): `npm run type-check`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/components/composer/manage-assignees-dialog.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): add ManageAssigneesDialog for workflow sharing

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task B7: Wire the dialog into admin + owner settings; badge shared flows

**Files:**
- Modify: `frontend/app/admin/workflows/page.tsx`
- Modify: `frontend/app/designer/[workflowId]/settings/page.tsx`
- Modify: `frontend/components/composer/designer-workflow-card.tsx`

- [ ] **Step 1: Admin entry point**

In `frontend/app/admin/workflows/page.tsx`, import the dialog:

```tsx
import { ManageAssigneesDialog } from "@/components/composer/manage-assignees-dialog";
```

Add it next to `ReassignOwnerDialog` in the actions cell:

```tsx
                  <TableCell className="flex items-center gap-2">
                    <ReassignOwnerDialog
                      workflowId={workflow.id}
                      workflowName={workflow.name}
                    />
                    <ManageAssigneesDialog
                      workflowId={workflow.id}
                      workflowName={workflow.name}
                    />
                    <Button
```

- [ ] **Step 2: Owner entry point**

In `frontend/app/designer/[workflowId]/settings/page.tsx`, import the dialog:

```tsx
import { ManageAssigneesDialog } from "@/components/composer/manage-assignees-dialog";
```

Add a new section after the "Mark as template" toggle block (after its closing `</div>` around line 248), inside the `<form>`:

```tsx
        {/* Sharing — grants full read/write access to other users without
            changing ownership.  All-or-nothing: no view-vs-edit split yet. */}
        <div className="flex items-center justify-between rounded-lg border px-4 py-3">
          <div>
            <p className="text-sm font-medium">Shared access</p>
            <p className="text-xs text-muted-foreground">
              Give other users full access to view and edit this workflow.
            </p>
          </div>
          <ManageAssigneesDialog workflowId={workflowId} workflowName={workflow.name} />
        </div>
```

- [ ] **Step 3: Badge shared-not-owned cards**

In `frontend/components/composer/designer-workflow-card.tsx`, extend the `Workflow` type and props:

```tsx
type Workflow = {
  id: string;
  name: string;
  description?: string | null;
  isPublic: boolean;
  isProduction: boolean;
  userId?: string | null;
};

export function DesignerWorkflowCard({
  wf,
  currentUserId,
}: {
  wf: Workflow;
  currentUserId?: string;
}) {
```

In the badge row, add a "Shared" badge when the viewer isn't the owner:

```tsx
          <div className="flex flex-wrap gap-1">
            {wf.isPublic && <Badge variant="secondary">Public</Badge>}
            {wf.isProduction && <Badge variant="default">Production</Badge>}
            {currentUserId && wf.userId && wf.userId !== currentUserId && (
              <Badge variant="outline">Shared with you</Badge>
            )}
          </div>
```

In `frontend/app/designer/page.tsx`, pass the current user id (from the session) through:

```tsx
import { useSession } from "next-auth/react";
```

```tsx
export default function DesignerHome() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const { data: session } = useSession();
  const currentUserId = (session as { user?: { id?: string } } | null)?.user?.id;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["designer-workflows"],
    queryFn: () => listWorkflows({ mine: true, limit: 100 }),
  });
```

```tsx
          {data.items.map((wf) => (
            <DesignerWorkflowCard key={wf.id} wf={wf} currentUserId={currentUserId} />
          ))}
```

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npm run type-check`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/app/admin/workflows/page.tsx frontend/app/designer/[workflowId]/settings/page.tsx frontend/components/composer/designer-workflow-card.tsx frontend/app/designer/page.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): wire ManageAssigneesDialog into admin + owner UI, badge shared flows

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task B8: Integration test + ADR-0025

**Files:**
- Create: `tests/integration/test_workflow_assignments.py`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_workflow_assignments.py`:

```python
"""Integration — workflow assignment grant/revoke against real Neon."""

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

_MINIMAL_BODY: dict[str, Any] = {
    "name": "Assignment integration test",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


async def test_grant_and_revoke_assignment_cycle(client: AsyncClient) -> None:
    r = await client.post("/workflows", json=_MINIMAL_BODY)
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    # Grant to a second user (dev-mode fallback only gives us one identity,
    # so this exercises the endpoint contract against a synthetic id — the
    # unit tests in tests/unit/api/test_workflow_assignments.py cover the
    # cross-user authz matrix with mocked identities).
    r = await client.post(f"/workflows/{wf_id}/assignments/some-other-user-id")
    assert r.status_code == 201, r.text

    r = await client.get(f"/workflows/{wf_id}/assignments")
    assert r.status_code == 200
    assert any(a["userId"] == "some-other-user-id" for a in r.json())

    r = await client.delete(f"/workflows/{wf_id}/assignments/some-other-user-id")
    assert r.status_code == 204

    r = await client.get(f"/workflows/{wf_id}/assignments")
    assert r.json() == []

    await client.delete(f"/workflows/{wf_id}")
```

- [ ] **Step 2: Run the integration test**

Run: `uv run pytest tests/integration/test_workflow_assignments.py -v -m integration`
Expected: 1 passed (requires `DATABASE_URL` pointed at the dev Neon instance)

- [ ] **Step 3: Add ADR-0025**

Append to `docs/decisions.md`:

```markdown
---

## ADR-0025: Workflow assignment — additive join table, all-or-nothing access

**Status:** Accepted (2026-07-09)

**Context.** A workflow could only ever have one owner (`Workflow.userId`). Investigation of OAB's actual schema (`convex/schema.ts`) found OAB never supported many-to-many assignment either — it's a genuine enhancement over OAB's original design, not a restoration.

**Decision.** Add `WorkflowAssignment` (`workflowId`, `userId`, `assignedById`, `assignedAt`; unique on `(workflowId, userId)`) as a layer on top of the existing single-owner field, which keeps its meaning unchanged ("who created this / who transfers/deletes it"). Assignment grants full read+write access (open, edit, run) — there is no view-vs-edit split. Only the owner or an admin can grant/revoke assignments; delete and owner-transfer remain owner/admin-only, untouched by assignment. A new `GET /users/search` endpoint (any authenticated active user, results limited to id/email/displayName) lets non-admin owners find people to share with, since the existing `/admin/users` listing is admin-only.

**Consequences.**
- `GET /workflows/{id}` and `PUT /workflows/{id}` authorization both gained an assignee check (`_has_assignment`) alongside the existing owner/admin/isPublic checks. `DELETE` and `/owner` transfer were deliberately left alone.
- `list_workflows`'s `mine=true` and default (non-admin) views both needed a third `OR` clause (`assignments.some.userId`) so assigned-not-owned workflows are actually visible anywhere in the UI — easy to miss since the read-path fix alone doesn't make a workflow discoverable.
- Per-assignee permission levels (view-only, etc.) are explicitly out of scope; if needed later, it's a new column on `WorkflowAssignment`, not a schema rework.

**Implemented by.** `docs/archive/phase-history/plans/2026-07-09-account-workflow-sharing-plan.md`, Part B.

**Related.** ADR-0021 (Phase 8 security policy — owner-only 404 pattern), ADR-0024.
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_workflow_assignments.py docs/decisions.md
git commit -m "$(cat <<'EOF'
test(integration): add workflow assignment cycle test; docs: add ADR-0025

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Part C — Autosave + unsaved-changes safeguard

### Task C1: `useAutosave` hook

**Files:**
- Create: `frontend/lib/use-autosave.ts`
- Test: `frontend/lib/use-autosave.test.ts` (new)

- [ ] **Step 1: Write the failing tests**

Create `frontend/lib/use-autosave.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useAutosave } from "./use-autosave";

describe("useAutosave", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts idle and moves to dirty on markDirty", () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAutosave(save, 3000));
    expect(result.current.status).toBe("idle");
    act(() => result.current.markDirty());
    expect(result.current.status).toBe("dirty");
  });

  it("debounces rapid markDirty calls into a single save", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAutosave(save, 3000));

    act(() => result.current.markDirty());
    act(() => vi.advanceTimersByTime(1000));
    act(() => result.current.markDirty());
    expect(save).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("saved");
  });

  it("sets status to error when save rejects", async () => {
    const save = vi.fn().mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAutosave(save, 1000));

    act(() => result.current.markDirty());
    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.status).toBe("error");
  });

  it("saveNow bypasses the debounce and saves immediately", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useAutosave(save, 3000));

    act(() => result.current.markDirty());
    await act(async () => {
      await result.current.saveNow();
    });

    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("saved");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- lib/use-autosave.test.ts`
Expected: FAIL — module `./use-autosave` doesn't exist

- [ ] **Step 3: Implement**

Create `frontend/lib/use-autosave.ts`:

```ts
import { useEffect, useRef, useState } from "react";

export type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "error";

/**
 * Debounced autosave: call `markDirty()` on every change to the tracked
 * data. `save` fires `debounceMs` after the last `markDirty()` call,
 * coalescing rapid edits into a single request. `saveNow()` bypasses the
 * debounce for an explicit manual Save action, sharing the same status
 * so the UI has one source of truth for "is my work saved."
 */
export function useAutosave(save: () => Promise<void>, debounceMs = 3000) {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  async function runSave() {
    setStatus("saving");
    try {
      await saveRef.current();
      setStatus("saved");
    } catch (err) {
      setStatus("error");
      throw err;
    }
  }

  function markDirty() {
    setStatus("dirty");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      void runSave();
    }, debounceMs);
  }

  async function saveNow() {
    if (timerRef.current) clearTimeout(timerRef.current);
    await runSave();
  }

  return { status, markDirty, saveNow };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- lib/use-autosave.test.ts`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/use-autosave.ts frontend/lib/use-autosave.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): add useAutosave debounce + save-status hook

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task C2: Wire autosave into the Designer canvas page

**Files:**
- Modify: `frontend/app/designer/[workflowId]/page.tsx`
- Modify: `frontend/components/composer/canvas/save-controls.tsx`

- [ ] **Step 1: Update `SaveControls` to show save status**

In `frontend/components/composer/canvas/save-controls.tsx`, add a `saveStatus` prop and render it:

```tsx
import type { SaveStatus } from "@/lib/use-autosave";

interface SaveControlsProps {
  workflowId: string;
  isSaving?: boolean;
  saveStatus: SaveStatus;
  onSave: () => Promise<void>;
  getCurrentNodes: () => Array<{ type: string; data: Record<string, unknown> }>;
  onExecutionStarted: (executionId: string) => void;
}

export function SaveControls({
  workflowId,
  isSaving = false,
  saveStatus,
  onSave,
  getCurrentNodes,
  onExecutionStarted,
}: SaveControlsProps) {
```

Add a status label before the buttons in the returned JSX:

```tsx
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-muted-foreground">
        {saveStatus === "saving"
          ? "Saving…"
          : saveStatus === "error"
            ? "Save failed"
            : saveStatus === "dirty"
              ? "Unsaved changes"
              : saveStatus === "saved"
                ? "All changes saved"
                : ""}
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleSave}
          disabled={isSaving || isPreparing}
        >
          {isSaving ? "Saving…" : "Save"}
        </Button>
        <Button size="sm" onClick={handleRunDraft} disabled={isSaving || isPreparing}>
          {isPreparing ? "Saving…" : "Run draft"}
        </Button>
        <RunDraftDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          workflowId={workflowId}
          workflow={{ nodes: getCurrentNodes() }}
          onStarted={(executionId) => onExecutionStarted(executionId)}
        />
      </div>
    </div>
  );
```

- [ ] **Step 2: Wire the hook into the canvas page**

In `frontend/app/designer/[workflowId]/page.tsx`, import the hook:

```tsx
import { useAutosave } from "@/lib/use-autosave";
```

After `saveMutation` is declared, add the hook and replace `handleSave`:

```tsx
  const autosave = useAutosave(async () => {
    await saveMutation.mutateAsync();
  }, 3000);

  async function handleSave() {
    setIsSaving(true);
    try {
      await autosave.saveNow();
    } finally {
      setIsSaving(false);
    }
  }
```

Wire `markDirty` into the canvas change handlers:

```tsx
          <WorkflowCanvas
            initialNodes={rfNodes}
            initialEdges={rfEdges}
            onNodesChange={(nodes) => {
              nodesRef.current = nodes;
              autosave.markDirty();
            }}
            onEdgesChange={(edges) => {
              edgesRef.current = edges;
              autosave.markDirty();
            }}
            runState={runState ?? undefined}
          />
```

Pass `saveStatus` to `SaveControls`:

```tsx
          <SaveControls
            workflowId={workflowId}
            isSaving={isSaving}
            saveStatus={autosave.status}
            onSave={handleSave}
            getCurrentNodes={() =>
              nodesRef.current.map((n) => ({
                type: String(n.type ?? ""),
                data: (n.data as Record<string, unknown>) ?? {},
              }))
            }
            onExecutionStarted={(executionId) => {
              setDraftExecutionId(executionId);
              setRunState(null);
            }}
          />
```

Add the `beforeunload` guard, right after the `isSaving`/`draftExecutionId`/`runState` `useState` declarations:

```tsx
  useEffect(() => {
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      if (autosave.status === "dirty" || autosave.status === "saving") {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [autosave.status]);
```

Add `useEffect` to the React import at the top of the file:

```tsx
import { useEffect, useRef, useState } from "react";
```

- [ ] **Step 3: Type-check**

Run (from `frontend/`): `npm run type-check`
Expected: no errors

- [ ] **Step 4: Manual verification**

Run: `npm run dev` (frontend) and `uv run uvicorn src.main:app --reload` (backend). Open a workflow in the Designer, drag a node, wait 3 seconds without touching the Save button, and confirm: (a) the status label reads "Unsaved changes" then "Saving…" then "All changes saved" without clicking Save; (b) refreshing the page shows the dragged node persisted; (c) making a change and immediately trying to close the tab triggers the browser's leave-site confirmation.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/designer/\[workflowId\]/page.tsx frontend/components/composer/canvas/save-controls.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): wire autosave + save-status indicator into the Designer canvas

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task C3: ADR-0026

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1: Append the ADR**

```markdown
---

## ADR-0026: Autosave replaces "transfer loses flow data" root cause

**Status:** Accepted (2026-07-09)

**Context.** A reported bug — "reassigning a workflow's owner leaves only Start+End nodes" — turned out, on investigation, to have no matching code path: the reassign endpoint only ever updates `Workflow.userId`. The Designer had no autosave at all; nodes/edges only reached Postgres via an explicit Save click. The real failure mode is that an owner can edit a flow, never click Save, and the database row genuinely only ever holds the Start+End scaffold from creation — which becomes visible the moment a different person (a new assignee, following ADR-0025) opens it fresh.

**Decision.** Add `useAutosave` (`frontend/lib/use-autosave.ts`): a debounced (3s) autosave that reuses the existing manual-save mutation and `PUT /workflows/{id}` path — no new backend endpoint. A `saveNow()` variant backs the manual Save button so both paths share one status state machine (`idle → dirty → saving → saved/error`), surfaced next to the Save button, plus a `beforeunload` guard that blocks tab-close while unsaved. No backend or transfer-endpoint change was needed or made.

**Consequences.**
- Closes the silent-data-loss gap without touching `assign_workflow_owner` or any transfer-related code, since that was never the cause.
- Last-write-wins remains the concurrency model; no multi-editor conflict resolution was introduced (explicit non-goal, consistent with the current single-editor assumption).
- If a genuine transfer-triggered data-loss bug surfaces later (i.e., reproduced with confirmed pre-save content), it is a different, new investigation — this ADR only closes the no-autosave gap that explained the reported symptom.

**Implemented by.** `docs/archive/phase-history/plans/2026-07-09-account-workflow-sharing-plan.md`, Part C.

**Related.** ADR-0025.
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions.md
git commit -m "$(cat <<'EOF'
docs: add ADR-0026 for the autosave design

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Backend full suite:** `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests && uv run pytest`
- [ ] **Frontend full suite:** (from `frontend/`) `npm run lint && npm run type-check && npm test`
- [ ] **Manual smoke test (per `verify` skill):** run both servers, and walk: (1) admin resets a user's password → user logs in with temp password → forced to `/change-password` → succeeds → normal session; (2) owner shares a workflow via "Manage access" → assignee sees it under "My workflows" with a "Shared with you" badge → assignee edits and saves it; (3) edit a flow, wait for autosave without clicking Save, refresh, confirm content persisted.
