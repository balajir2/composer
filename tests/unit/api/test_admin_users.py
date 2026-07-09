"""Unit tests for admin user management endpoints (Phase 10d)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.auth import ensure_admin
from src.security.rate_limit import RateLimiter


def _client_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.user = MagicMock()
    db.apikey = MagicMock()
    db.llmapikey = MagicMock()
    db.deploymentsetting = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()

    app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]

    return TestClient(app), db


def _client_member(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)  # dev-mode 'dev' → member
    db.llmapikey = MagicMock()
    db.deploymentsetting = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    # No dependency_overrides → ensure_admin returns 403 for non-admin
    return TestClient(app), db


def _user_row(
    uid: str = "user-1",
    email: str = "user@example.com",
    role_val: str = "member",
    display_name: str | None = None,
) -> SimpleNamespace:
    role = SimpleNamespace(value=role_val)
    return SimpleNamespace(
        id=uid,
        email=email,
        role=role,
        displayName=display_name,
        passwordHash="$2b$12$hashed",
        createdAt="2026-04-22T00:00:00Z",
        updatedAt="2026-04-22T00:00:00Z",
    )


def _apikey_row(
    kid: str = "key-1",
    user_id: str = "user-1",
    revoked: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=kid,
        userId=user_id,
        label="my key",
        keyHash="hash123",
        keyPrefix="ck_ab",
        createdAt="2026-04-22T00:00:00Z",
        lastUsedAt=None,
        expiresAt=None,
        revokedAt="2026-04-22T01:00:00Z" if revoked else None,
    )


def test_list_users_returns_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin sees all users; no passwordHash in response."""
    client, db = _client_admin(monkeypatch)
    db.user.find_many = AsyncMock(
        return_value=[
            _user_row("u1", "alice@example.com", "admin"),
            _user_row("u2", "bob@example.com", "member"),
        ]
    )
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    emails = {u["email"] for u in body}
    assert emails == {"alice@example.com", "bob@example.com"}
    for u in body:
        assert "passwordHash" not in u
        assert "password_hash" not in u


def test_promote_to_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST role flips member → admin and returns updated user."""
    client, db = _client_admin(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row("u1", "member@example.com", "member"))
    db.user.update = AsyncMock(return_value=_user_row("u1", "member@example.com", "admin"))
    resp = client.post("/admin/users/u1/role", json={"role": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert body["email"] == "member@example.com"
    db.user.update.assert_awaited_once()


def test_promote_unknown_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST role on missing user returns 404."""
    client, db = _client_admin(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=None)
    resp = client.post("/admin/users/nonexistent/role", json={"role": "admin"})
    assert resp.status_code == 404


def test_admin_revoke_api_key_soft_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revoke endpoint soft-deletes by setting revokedAt."""
    client, db = _client_admin(monkeypatch)
    db.apikey.find_unique = AsyncMock(return_value=_apikey_row("key-1", "user-1", revoked=False))
    db.apikey.update = AsyncMock()
    resp = client.post("/admin/users/user-1/api-keys/key-1/revoke")
    assert resp.status_code == 204
    db.apikey.update.assert_awaited_once()
    call_data = db.apikey.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert "revokedAt" in call_data


def test_admin_revoke_not_matching_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revoking a key that belongs to a different user returns 404."""
    client, db = _client_admin(monkeypatch)
    # key belongs to "other-user", not "user-1"
    db.apikey.find_unique = AsyncMock(
        return_value=_apikey_row("key-1", "other-user", revoked=False)
    )
    resp = client.post("/admin/users/user-1/api-keys/key-1/revoke")
    assert resp.status_code == 404


def test_member_cannot_list_users_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin member receives 403 on GET /admin/users."""
    client, _ = _client_member(monkeypatch)
    resp = client.get("/admin/users")
    assert resp.status_code == 403


# --- Admin-forced reset-password (Account + Workflow Sharing plan, Task A6) ---


def test_admin_reset_password_returns_temp_password_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset endpoint returns a temporary password of adequate length, once."""
    client, db = _client_admin(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row("u1", "alice@example.com", "member"))
    db.user.update = AsyncMock(return_value=_user_row("u1", "alice@example.com", "member"))
    resp = client.post("/admin/users/u1/reset-password")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "temporaryPassword" in body
    assert len(body["temporaryPassword"]) >= 12


def test_admin_reset_password_sets_must_change_password_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user row is updated with a fresh hash and mustChangePassword=True."""
    client, db = _client_admin(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row("u1", "alice@example.com", "member"))
    db.user.update = AsyncMock(return_value=_user_row("u1", "alice@example.com", "member"))
    client.post("/admin/users/u1/reset-password")
    db.user.update.assert_awaited_once()
    call_data = db.user.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert call_data["mustChangePassword"] is True
    assert "passwordHash" in call_data
    assert call_data["passwordHash"] != ""


def test_admin_reset_password_unknown_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resetting a nonexistent user returns 404."""
    client, db = _client_admin(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=None)
    resp = client.post("/admin/users/ghost/reset-password")
    assert resp.status_code == 404


def test_member_cannot_reset_password_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin member receives 403 on POST /admin/users/{id}/reset-password."""
    client, _ = _client_member(monkeypatch)
    resp = client.post("/admin/users/u1/reset-password")
    assert resp.status_code == 403
