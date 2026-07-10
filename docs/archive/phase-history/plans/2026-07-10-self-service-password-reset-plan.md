# Self-Service Password Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standard "Forgot password?" email-link flow on top of the existing admin-only password reset (ADR-0024), reusing the `password_change` JWT mechanism and the existing Resend email integration.

**Architecture:** Two new backend endpoints (`/auth/forgot-password`, `/auth/reset-password`) reuse `create_password_change_token`/`verify_password_change_token` from ADR-0024 — no new token type. A small email helper wraps the existing `ResendEmailProvider`. Two new frontend pages, one new login-page link, one middleware matcher tweak.

**Tech Stack:** FastAPI + Prisma Python (backend), Next.js 14 (frontend), pytest (backend tests). No new dependencies.

**Spec:** `docs/archive/phase-history/specs/2026-07-10-self-service-password-reset-design.md`

---

### Task 1: Config settings

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Add the new settings**

In `src/config.py`, change the existing line (around line 48):

```python
    jwt_password_change_ttl_seconds: int = 600  # 10 minutes — forced-reset completion window
```

to:

```python
    # Shared by the admin-forced reset (ADR-0024) and self-service email reset
    # (below) — 30 min balances the admin-handoff case (interactive, could be
    # shorter) against the email case (user needs time to check their inbox).
    jwt_password_change_ttl_seconds: int = 1800  # 30 minutes
```

Add two new settings after `resend_api_key` (around line 85):

```python
    resend_api_key: str = Field(default="", description="Resend API key for email delivery.")
    resend_from_email: str = Field(
        default="noreply@script-research.online",
        description="Verified Resend sender address for transactional emails (password reset, etc).",
    )
```

Add a new setting after `frontend_origins`/near other URL-shaped settings — add right after the `composer_frontend_origins` block (around line 119):

```python
    # ─── Backend-constructed links back to the frontend ─────
    frontend_url: str = Field(
        default="http://localhost:3000",
        description=(
            "Base URL of the Composer frontend, used to build absolute links "
            "in backend-sent emails (e.g. password-reset links). Production "
            "sets this to https://www.flowcomposer.online."
        ),
    )
```

Add a new rate-limit setting in the existing rate-limit block (around line 141), after `rate_limit_users_search_per_minute`:

```python
    rate_limit_users_search_per_minute: int = 30
    rate_limit_forgot_password_per_minute: int = 5
```

- [ ] **Step 2: Verify**

Run: `.venv/Scripts/python.exe -c "from src.config import get_settings; s = get_settings(); print(s.jwt_password_change_ttl_seconds, s.resend_from_email, s.frontend_url, s.rate_limit_forgot_password_per_minute)"`
Expected: `1800 noreply@script-research.online http://localhost:3000 5`

- [ ] **Step 3: Update `.env.example`**

Add to `.env.example` (find the existing `RESEND_API_KEY=` line and add these near it):

```
RESEND_FROM_EMAIL=noreply@script-research.online
FRONTEND_URL=http://localhost:3000
```

- [ ] **Step 4: Commit**

```bash
git add src/config.py .env.example
git commit -m "$(cat <<'EOF'
feat(config): add settings for self-service password reset emails

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

## Context

This is Task 1 of `docs/archive/phase-history/plans/2026-07-10-self-service-password-reset-plan.md`, Composer repo (`d:\GitHub\composer`). This is a small, standalone settings change — no dependencies. `jwt_password_change_ttl_seconds` already exists (added for ADR-0024's admin-forced-reset flow) and is being repurposed/bumped, not newly added — read the current file first to confirm the exact current line before editing, since the plan's quoted "before" text must match reality.

**Do not touch `pyproject.toml`, CI config, or the Prisma schema in this task** — this is a plain settings-file change only.

---

### Task 2: Email helper

**Files:**
- Modify: `src/integrations/email/resend.py`
- Test: `tests/unit/integrations/test_resend_password_reset.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/integrations/test_resend_password_reset.py` (create the `tests/unit/integrations/` directory if it doesn't already exist — check first):

```python
"""Tests for send_password_reset_email (self-service password reset plan)."""

from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.email.resend import send_password_reset_email


@pytest.mark.asyncio
async def test_send_password_reset_email_builds_correct_payload() -> None:
    with patch(
        "src.integrations.email.resend.ResendEmailProvider.send_email",
        new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = {"id": "email-123"}
        await send_password_reset_email(
            to="alice@example.com",
            reset_link="https://www.flowcomposer.online/reset-password?token=abc123",
        )

    mock_send.assert_awaited_once()
    payload = mock_send.await_args.kwargs.get("payload") or mock_send.await_args.args[0]
    assert payload["to"] == ["alice@example.com"]
    assert "reset" in payload["subject"].lower()
    assert "https://www.flowcomposer.online/reset-password?token=abc123" in payload["html"]


@pytest.mark.asyncio
async def test_send_password_reset_email_uses_configured_from_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@script-research.online")
    from src.config import get_settings

    get_settings.cache_clear()
    with patch(
        "src.integrations.email.resend.ResendEmailProvider.send_email",
        new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = {"id": "email-123"}
        await send_password_reset_email(to="bob@example.com", reset_link="https://x/reset?t=1")

    payload = mock_send.await_args.kwargs.get("payload") or mock_send.await_args.args[0]
    assert payload["from"] == "noreply@script-research.online"
    get_settings.cache_clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/integrations/test_resend_password_reset.py -v`
Expected: FAIL with `ImportError: cannot import name 'send_password_reset_email'`

- [ ] **Step 3: Implement**

In `src/integrations/email/resend.py`, add after the `ResendEmailProvider` class (before `__all__`):

```python
async def send_password_reset_email(to: str, reset_link: str) -> None:
    """Send a password-reset email via Resend.

    Errors are intentionally allowed to propagate to the caller — the
    /auth/forgot-password route catches them and still returns 204 (see
    ADR-0024/spec: never let email-delivery failure leak account-existence
    information to the caller).
    """
    from src.config import get_settings

    settings = get_settings()
    html = (
        "<p>Someone requested a password reset for your Composer account.</p>"
        f'<p><a href="{reset_link}">Click here to set a new password</a>. '
        "This link expires in 30 minutes.</p>"
        "<p>If you didn't request this, you can safely ignore this email.</p>"
    )
    payload: dict[str, Any] = {
        "from": settings.resend_from_email,
        "to": [to],
        "subject": "Reset your Composer password",
        "html": html,
    }
    await ResendEmailProvider(settings.resend_api_key).send_email(payload)
```

Update `__all__`:

```python
__all__ = [
    "RESEND_API_BASE",
    "ResendEmailProvider",
    "ResendEmailProviderError",
    "send_password_reset_email",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/integrations/test_resend_password_reset.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/integrations/email/resend.py tests/unit/integrations/test_resend_password_reset.py
git commit -m "$(cat <<'EOF'
feat(email): add send_password_reset_email helper

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

## Context

This is Task 2 of the plan. Task 1 (config settings, including `resend_from_email`) is done and committed. `src/integrations/email/resend.py` already has `ResendEmailProvider` (a plain async HTTP client wrapping Resend's `POST /emails`, already used by the workflow email-node executor at `src/executors/email.py`) — read the current file first, since this task adds ONE new function to it, nothing else changes. The `payload` dict shape (`from`/`to`/`subject`/`html`) matches what `ResendEmailProvider.send_email` already expects (see `src/executors/email.py:76-87` for the existing usage pattern) — `to` is a list of strings per Resend's API contract (confirm this against the existing executor's payload construction before assuming).

This is a TDD task.

---

### Task 3: `POST /auth/forgot-password`

**Files:**
- Modify: `src/api/auth_standalone.py`
- Test: `tests/unit/api/test_auth_forgot_password.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_auth_forgot_password.py`:

```python
"""Tests for POST /auth/forgot-password (self-service password reset plan)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
        "isActive": True,
        "mustChangePassword": False,
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
    app.state.db = db
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_forgot_password_existing_user_sends_email_and_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    with patch(
        "src.api.auth_standalone.send_password_reset_email", new_callable=AsyncMock
    ) as mock_send:
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 204, resp.text
    mock_send.assert_awaited_once()
    call_kwargs = mock_send.await_args.kwargs
    assert call_kwargs["to"] == "alice@example.com"
    assert "/reset-password?token=" in call_kwargs["reset_link"]


def test_forgot_password_unknown_email_still_returns_204_no_email_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=None)
    with patch(
        "src.api.auth_standalone.send_password_reset_email", new_callable=AsyncMock
    ) as mock_send:
        resp = client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert resp.status_code == 204, resp.text
    mock_send.assert_not_awaited()


def test_forgot_password_sso_only_account_returns_204_no_email_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row(passwordHash=None))
    with patch(
        "src.api.auth_standalone.send_password_reset_email", new_callable=AsyncMock
    ) as mock_send:
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 204, resp.text
    mock_send.assert_not_awaited()


def test_forgot_password_email_send_failure_still_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Resend outage must not leak account-existence info via a 500."""
    client, db = _client(monkeypatch)
    with patch(
        "src.api.auth_standalone.send_password_reset_email",
        new_callable=AsyncMock,
        side_effect=RuntimeError("resend is down"),
    ):
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 204, resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_auth_forgot_password.py -v`
Expected: FAIL — 404 (route doesn't exist)

- [ ] **Step 3: Implement**

In `src/api/auth_standalone.py`, add the import:

```python
from src.integrations.email.resend import send_password_reset_email
```

Add a request model near `ChangePasswordRequest`:

```python
class ForgotPasswordRequest(BaseModel):
    email: EmailStr
```

Add the endpoint after `change_password` (before `__all__`):

```python
@router.post("/auth/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_forgot_password",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_forgot_password_per_minute),
    )
    user = await db.user.find_unique(where={"email": str(payload.email)})  # pyright: ignore[reportAttributeAccessIssue]
    if user is not None and user.passwordHash is not None:
        token = create_password_change_token(user.id)
        reset_link = f"{get_settings().frontend_url}/reset-password?token={token}"
        try:
            await send_password_reset_email(to=user.email, reset_link=reset_link)
        except Exception:
            # Never let an email-delivery failure change this endpoint's
            # response — that would leak account-existence information to
            # the caller. Delivery failures are operational, not the
            # caller's concern; log for ops visibility only.
            import logging

            logging.getLogger(__name__).warning(
                "forgot-password: failed to send reset email", exc_info=True
            )
    # Always 204, regardless of whether the email matched a real,
    # password-based account — no account-enumeration leak.
    return None
```

Update `__all__` to add `"ForgotPasswordRequest"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_auth_forgot_password.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/api/auth_standalone.py tests/unit/api/test_auth_forgot_password.py
git commit -m "$(cat <<'EOF'
feat(auth): add POST /auth/forgot-password

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

## Context

This is Task 3 of the plan. Task 1 (config) and Task 2 (`send_password_reset_email`) are done and committed. `create_password_change_token` is already imported in `src/api/auth_standalone.py` (used by the existing `login()` mustChangePassword branch) — no new import needed for it. Read the current `src/api/auth_standalone.py` in full first, since this task inserts a new endpoint and must not disturb any existing route.

**Security-critical property to preserve exactly:** this endpoint must return `204` in EVERY case — unknown email, SSO-only account, and even if the email provider throws — so that no response ever distinguishes "this email exists" from "this email doesn't exist." Do not let any exception escape this route as a 500; catch broadly around the email-send call specifically (not around the whole handler, so a genuine bug elsewhere still surfaces normally in other tests/paths).

This is a TDD task.

---

### Task 4: `POST /auth/reset-password`

**Files:**
- Modify: `src/api/auth_standalone.py`
- Test: `tests/unit/api/test_auth_reset_password.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_auth_reset_password.py`:

```python
"""Tests for POST /auth/reset-password (self-service password reset plan)."""

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
        "isActive": True,
        "mustChangePassword": False,
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


def test_reset_password_with_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_password_change_token

    client, db = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 204, resp.text
    db.user.update.assert_awaited_once()
    data = db.user.update.await_args.kwargs["data"]
    assert data["mustChangePassword"] is False
    assert data["passwordHash"] != "brand-new-password-99"


def test_reset_password_does_not_require_current_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: no currentPassword field should be needed or checked."""
    from src.security.jwt import create_password_change_token

    client, _ = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 204, resp.text


def test_reset_password_rejects_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_access_token

    client, _ = _client(monkeypatch)
    token = create_access_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 400


def test_reset_password_rejects_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_refresh_token

    client, _ = _client(monkeypatch)
    token = create_refresh_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 400


def test_reset_password_rejects_malformed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch)
    resp = client.post(
        "/auth/reset-password",
        json={"token": "not.a.jwt", "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 400


def test_reset_password_short_new_password_422(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_password_change_token

    client, _ = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "short"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_auth_reset_password.py -v`
Expected: FAIL — 404 (route doesn't exist)

- [ ] **Step 3: Implement**

In `src/api/auth_standalone.py`, add the import:

```python
from src.security.jwt import (
    TokenVerificationError,
    create_access_token,
    create_password_change_token,
    create_refresh_token,
    verify_password_change_token,
    verify_refresh_token,
)
```

(add `verify_password_change_token` to the existing import block from `src.security.jwt` — do not duplicate the import statement, merge into the existing one.)

Add a request model near `ForgotPasswordRequest`:

```python
class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: str
    new_password: str = Field(min_length=8, alias="newPassword")
```

Add the endpoint after `forgot_password` (before `__all__`):

```python
@router.post("/auth/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_forgot_password",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_forgot_password_per_minute),
    )
    try:
        claims = verify_password_change_token(payload.token)
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid or expired reset token: {exc}",
        ) from exc
    user_id = claims.sub
    await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id},
        data={
            "passwordHash": hash_password(payload.new_password),
            "mustChangePassword": False,
        },
    )
    return None
```

Update `__all__` to add `"ForgotPasswordRequest"` and `"ResetPasswordRequest"` (alphabetically).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_auth_reset_password.py -v`
Expected: 6 passed

- [ ] **Step 5: Regression check**

Run: `uv run pytest tests/unit/api/test_auth_standalone.py tests/unit/api/test_auth_change_password.py tests/unit/api/test_auth_forgot_password.py -v`
Expected: all still passed

- [ ] **Step 6: Commit**

```bash
git add src/api/auth_standalone.py tests/unit/api/test_auth_reset_password.py
git commit -m "$(cat <<'EOF'
feat(auth): add POST /auth/reset-password

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

## Context

This is Task 4 of the plan. Task 3 (`/auth/forgot-password`) is done and committed. This endpoint deliberately calls `verify_password_change_token` DIRECTLY (not the `get_user_id_allow_password_change` dependency used by `/auth/change-password`), because that dependency also accepts normal access tokens — `/auth/reset-password` must accept ONLY a `password_change` token, since it's reachable by a fully anonymous, logged-out caller with no other session. This is the key security property this task must get right: an access token or refresh token must NOT work here.

Read the current `src/api/auth_standalone.py` in full first (it now has `register`/`login`/`refresh`/`disconnect`/`change_password`/`forgot_password` from prior tasks) — confirm the exact current import block for `src.security.jwt` before editing it (merge `verify_password_change_token` into the existing import statement rather than adding a duplicate one).

This is a TDD task.

---

### Task 5: Frontend API client functions

**Files:**
- Modify: `frontend/lib/composer-api.ts`
- Modify: `frontend/lib/auth.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/lib/auth.test.ts` (inside the existing `describe("composer session helpers", ...)` block):

```ts
  it("composerForgotPassword posts email and resolves on 204", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204 });
    const { composerForgotPassword } = await import("@/lib/composer-api");
    await composerForgotPassword("alice@example.com");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/forgot-password"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ email: "alice@example.com" }),
      })
    );
  });

  it("composerForgotPassword does not throw on non-OK (never leak account existence)", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 429 });
    const { composerForgotPassword } = await import("@/lib/composer-api");
    await expect(composerForgotPassword("alice@example.com")).resolves.toBeUndefined();
  });

  it("composerResetPassword posts token and new password, resolves on 204", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204 });
    const { composerResetPassword } = await import("@/lib/composer-api");
    await composerResetPassword("tok-1", "new-pw-12345678");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/reset-password"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ token: "tok-1", newPassword: "new-pw-12345678" }),
      })
    );
  });

  it("composerResetPassword throws on non-OK response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 400 });
    const { composerResetPassword } = await import("@/lib/composer-api");
    await expect(composerResetPassword("bad-token", "new-pw-12345678")).rejects.toThrow(
      /reset password failed/
    );
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npm test -- lib/auth.test.ts`
Expected: FAIL — `composerForgotPassword`/`composerResetPassword` are not exported

- [ ] **Step 3: Implement**

In `frontend/lib/composer-api.ts`, add after `composerChangePassword`:

```ts
/**
 * Deliberately swallows non-OK responses: the backend always returns 204
 * for this endpoint regardless of whether the email matched an account
 * (no account-enumeration leak), so there's nothing meaningful to throw
 * on here — a 429 from rate-limiting shouldn't surface as an error the
 * UI treats differently from "email sent."
 */
export async function composerForgotPassword(email: string): Promise<void> {
  await fetch(`${composerApiUrl}/auth/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export async function composerResetPassword(token: string, newPassword: string): Promise<void> {
  const res = await fetch(`${composerApiUrl}/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, newPassword }),
  });
  if (!res.ok) throw new Error(`reset password failed: ${res.status}`);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- lib/auth.test.ts`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/composer-api.ts frontend/lib/auth.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): add composerForgotPassword + composerResetPassword

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

## Context

This is Task 5 of the plan (backend Tasks 1-4 are done and committed). `frontend/lib/composer-api.ts` is a plain-fetch, no-NextAuth-dependency module (per its own header comment) — these two new functions follow that exact style, matching `composerChangePassword`'s shape. Read the current file and `frontend/lib/auth.test.ts` first to confirm the exact current structure before appending.

Note `composerForgotPassword` deliberately never throws on a non-OK response — this is intentional (see the docstring in the code above), not an oversight; don't "fix" it to throw.

This is a TDD task, frontend/vitest side.

---

### Task 6: Frontend pages, login link, middleware exclusion

**Files:**
- Modify: `frontend/app/(auth)/login/page.tsx`
- Create: `frontend/app/(auth)/forgot-password/page.tsx`
- Create: `frontend/app/(auth)/reset-password/page.tsx`
- Modify: `frontend/middleware.ts`

- [ ] **Step 1: Add the "Forgot password?" link to the login page**

In `frontend/app/(auth)/login/page.tsx`, find the password field's `<div className="space-y-2">` block and add a link below the `Label`/`Input` pair, inside the same `<div>`:

```tsx
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="password">Password</Label>
              <Link
                href="/forgot-password"
                className="text-xs text-muted-foreground underline-offset-4 hover:underline"
              >
                Forgot password?
              </Link>
            </div>
            <Input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
```

(`Link` from `next/link` is already imported in this file for the "Create an account" link — reuse it, don't add a duplicate import.)

- [ ] **Step 2: Create the forgot-password page**

Create `frontend/app/(auth)/forgot-password/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ComposerLogo } from "@/components/composer/composer-logo";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { composerForgotPassword } from "@/lib/composer-api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await composerForgotPassword(email);
    } finally {
      // Always show the same generic confirmation, whether or not the
      // email matched a real account — never let this page distinguish
      // the two outcomes (see backend's matching no-enumeration design).
      setSubmitting(false);
      setSubmitted(true);
    }
  }

  return (
    <Card className="w-full max-w-md shadow-lg">
      <CardHeader className="items-center text-center">
        <ComposerLogo size={48} className="mb-3 text-primary" />
        <CardTitle className="text-2xl">Reset your password</CardTitle>
        {!submitted && (
          <CardDescription>
            Enter your email and we&apos;ll send you a link to reset your password.
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        {submitted ? (
          <div className="space-y-4 text-center">
            <p className="text-sm text-muted-foreground">
              If an account exists for that email, we&apos;ve sent a link to reset your
              password. Check your inbox.
            </p>
            <Link
              href="/login"
              className="text-sm text-primary underline-offset-4 hover:underline"
            >
              Back to sign in
            </Link>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Sending…" : "Send reset link"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              <Link href="/login" className="text-primary underline-offset-4 hover:underline">
                Back to sign in
              </Link>
            </p>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Create the reset-password page**

Create `frontend/app/(auth)/reset-password/page.tsx`:

```tsx
"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ComposerLogo } from "@/components/composer/composer-logo";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { composerResetPassword } from "@/lib/composer-api";

// Same Suspense-wrapping requirement as the login page: useSearchParams()
// must live inside a <Suspense> boundary for Next.js 14 static generation.
export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordForm />
    </Suspense>
  );
}

function ResetPasswordForm() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token");
  const [newPassword, setNewPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) {
      toast.error("This reset link is invalid. Please request a new one.");
      return;
    }
    setSubmitting(true);
    try {
      await composerResetPassword(token, newPassword);
      toast.success("Password reset. Please sign in with your new password.");
      router.push("/login");
    } catch (err) {
      toast.error(
        err instanceof Error
          ? "This reset link is invalid or has expired. Please request a new one."
          : "Failed to reset password."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="w-full max-w-md shadow-lg">
      <CardHeader className="items-center text-center">
        <ComposerLogo size={48} className="mb-3 text-primary" />
        <CardTitle className="text-2xl">Set a new password</CardTitle>
        {!token && (
          <CardDescription className="text-destructive">
            This link is missing its reset token.{" "}
            <Link href="/forgot-password" className="underline">
              Request a new one
            </Link>
            .
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        <form onSubmit={onSubmit} className="space-y-4">
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
          <Button type="submit" className="w-full" disabled={submitting || !token}>
            {submitting ? "Resetting…" : "Reset password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Exclude the two new routes from middleware's matcher**

In `frontend/middleware.ts`, update the matcher:

```ts
export const config = {
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|login|register|forgot-password|reset-password).*)",
  ],
};
```

- [ ] **Step 5: Type-check and lint**

Run (from `frontend/`): `npm run type-check && npm run lint`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add "frontend/app/(auth)/login/page.tsx" "frontend/app/(auth)/forgot-password/page.tsx" "frontend/app/(auth)/reset-password/page.tsx" frontend/middleware.ts
git commit -m "$(cat <<'EOF'
feat(frontend): add forgot-password + reset-password pages

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

## Context

This is Task 6 of the plan (backend Tasks 1-4 and frontend Task 5 are done and committed). Read `frontend/app/(auth)/login/page.tsx` and `frontend/middleware.ts` in full first — they've been touched by prior session work (ADR-0024's forced-reset flow) and may have evolved slightly from what's quoted above; adapt precisely while preserving every existing behavior (Azure SSO button, the `mustChangePassword` redirect after login, the existing `beforeunload`-equivalent... there is none on this page, just don't remove anything).

**Important:** `/reset-password` must be reachable by a fully anonymous, logged-out visitor — do NOT add any `requireSession()`/`requireAnySession()` guard to it (unlike `/change-password`, which is a different, session-requiring flow from ADR-0024). This page's only "proof of identity" is the token in the URL, verified entirely on the backend.

Neither new page needs a layout-level session guard — they sit directly under `frontend/app/(auth)/layout.tsx` (the same plain layout `/login`/`/register` already use), which has no session logic. Confirm this is still true by reading `frontend/app/(auth)/layout.tsx` before assuming.

---

### Task 7: ADR-0027

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1: Append the ADR**

```markdown
---

## ADR-0027: Self-service password reset — additive to ADR-0024, same token mechanism

**Status.** Accepted 2026-07-10.

**Context.** ADR-0024 deliberately shipped admin-only password reset (no email dependency), a considered trade-off at the time. The user later asked for a standard self-service "Forgot password?" flow, explicitly wanting it reused from existing infrastructure rather than built as new auth machinery.

**Decision.** Add two endpoints that reuse the existing `password_change` JWT type/mechanism from ADR-0024, rather than inventing a second token concept: `POST /auth/forgot-password` (looks up the email, and — only for a real, password-based account — emails a link containing a fresh `password_change` token; always returns 204 regardless of whether the email matched anything, closing the account-enumeration vector) and `POST /auth/reset-password` (verifies the token directly via `verify_password_change_token`, sets the new password — critically, without asking for the current password, since the whole point is the user forgot it). Email delivery reuses the existing `ResendEmailProvider` already wired up for the workflow email-node executor — no new email infrastructure. The shared `jwt_password_change_ttl_seconds` was bumped from 10 to 30 minutes to serve both the admin-handoff case (interactive) and the new email case (user needs time to check their inbox).

**Consequences.**
- `/auth/reset-password` intentionally does NOT use the `get_user_id_allow_password_change` dependency (which also accepts normal access tokens) — it calls `verify_password_change_token` directly, since this route must be reachable by a fully anonymous caller and must reject access/refresh tokens outright.
- Resend's free-tier verified sending domain (`script-research.online`) is unrelated to and independent from the app's own domain (`flowcomposer.online`) — this is normal and doesn't need to change.
- Email-delivery failures inside `/auth/forgot-password` are deliberately swallowed (logged, not surfaced) so a Resend outage can't be used to distinguish "this email exists" from "this email doesn't," matching the endpoint's always-204 contract.

**Implemented by.** `docs/archive/phase-history/plans/2026-07-10-self-service-password-reset-plan.md` (commits on `main`, 2026-07-10).

**Related.** ADR-0024 (admin-only password reset — the mechanism this reuses).
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions.md
git commit -m "$(cat <<'EOF'
docs: add ADR-0027 for self-service password reset

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Backend:** `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests && uv run pytest tests/unit -q`
- [ ] **Frontend:** (from `frontend/`) `npm run lint && npm run type-check && npm test`
- [ ] **Manual/live check (once the Resend key decryption issue is fixed operationally — see spec's "out of scope" note):** submit `/forgot-password` with a real test account's email, confirm the email arrives from `noreply@script-research.online`, click the link, confirm `/reset-password` accepts the new password and redirects to `/login`, confirm login works with the new password.
