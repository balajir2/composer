"""Unit tests for admin user management endpoints (Phase 10d)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.models import UserRole
from src.db.repos import api_keys as keys_repo
from src.db.repos import users as users_repo
from src.db.repos.api_keys import ApiKeyRecord
from src.main import create_app
from src.security.auth import ensure_admin
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSession, make_user, override_session


def _app_client(monkeypatch: pytest.MonkeyPatch, *, admin: bool) -> tuple[TestClient, FakeSession]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    session = override_session(app)  # routes resolve their session before the admin gate
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    if admin:
        app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    # else: ensure_admin is not overridden → 403 for the dev member
    return TestClient(app), session


def _client_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
    return _app_client(monkeypatch, admin=True)


def _client_member(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
    return _app_client(monkeypatch, admin=False)


def _apikey_row(
    kid: str = "key-1",
    user_id: str = "user-1",
    revoked: bool = False,
) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=kid,
        user_id=user_id,
        label="my key",
        key_hash="hash123",
        key_prefix="ck_ab",
        created_at=datetime(2026, 4, 22, tzinfo=UTC),
        last_used_at=None,
        expires_at=None,
        revoked_at=datetime(2026, 4, 22, 1, tzinfo=UTC) if revoked else None,
    )


def test_list_users_returns_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin sees all users; no passwordHash in response."""
    client, _ = _client_admin(monkeypatch)
    monkeypatch.setattr(
        users_repo,
        "list_all",
        AsyncMock(
            return_value=[
                make_user(id="u1", email="alice@example.com", role=UserRole.ADMIN),
                make_user(id="u2", email="bob@example.com"),
            ]
        ),
    )
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {u["email"] for u in body} == {"alice@example.com", "bob@example.com"}
    for u in body:
        assert "passwordHash" not in u
        assert "password_hash" not in u


def test_promote_to_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST role flips member → admin and returns updated user."""
    client, session = _client_admin(monkeypatch)
    set_role = AsyncMock(
        return_value=make_user(id="u1", email="member@example.com", role=UserRole.ADMIN)
    )
    monkeypatch.setattr(users_repo, "set_role", set_role)
    resp = client.post("/admin/users/u1/role", json={"role": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert body["email"] == "member@example.com"
    set_role.assert_awaited_once()
    assert set_role.await_args.args[1:] == ("u1", UserRole.ADMIN)  # type: ignore[union-attr]
    assert session.commit.await_count == 1


def test_promote_unknown_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST role on missing user returns 404."""
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(users_repo, "set_role", AsyncMock(return_value=None))
    resp = client.post("/admin/users/nonexistent/role", json={"role": "admin"})
    assert resp.status_code == 404
    assert session.commit.await_count == 0


def test_deactivate_revokes_keys_and_deactivates_in_one_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="u1")))
    revoke_all = AsyncMock(return_value=2)
    set_active = AsyncMock(return_value=make_user(id="u1", is_active=False))
    monkeypatch.setattr(keys_repo, "revoke_all_active_for_user", revoke_all)
    monkeypatch.setattr(users_repo, "set_active", set_active)
    resp = client.delete("/admin/users/u1")
    assert resp.status_code == 200
    assert resp.json()["isActive"] is False
    revoke_all.assert_awaited_once()
    assert revoke_all.await_args.args[1] == "u1"  # type: ignore[union-attr]
    set_active.assert_awaited_once()
    assert set_active.await_args.args[1:] == ("u1", False)  # type: ignore[union-attr]
    assert session.commit.await_count == 1


def test_deactivate_user_deleted_concurrently_rolls_back_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="u1")))
    monkeypatch.setattr(keys_repo, "revoke_all_active_for_user", AsyncMock(return_value=0))
    monkeypatch.setattr(users_repo, "set_active", AsyncMock(return_value=None))
    resp = client.delete("/admin/users/u1")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "user 'u1' not found"
    assert session.rollback.await_count == 1
    assert session.commit.await_count == 0


def test_deactivate_unknown_user_404_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=None))
    revoke_all = AsyncMock()
    monkeypatch.setattr(keys_repo, "revoke_all_active_for_user", revoke_all)
    assert client.delete("/admin/users/nope").status_code == 404
    revoke_all.assert_not_awaited()
    assert session.commit.await_count == 0


def test_reactivate_does_not_restore_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    set_active = AsyncMock(return_value=make_user(id="u1", is_active=True))
    monkeypatch.setattr(users_repo, "set_active", set_active)
    revoke_all = AsyncMock()
    monkeypatch.setattr(keys_repo, "revoke_all_active_for_user", revoke_all)
    resp = client.post("/admin/users/u1/reactivate")
    assert resp.status_code == 200
    assert set_active.await_args.args[1:] == ("u1", True)  # type: ignore[union-attr]
    revoke_all.assert_not_awaited()
    assert session.commit.await_count == 1


def test_admin_revoke_api_key_soft_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revoke endpoint soft-deletes by setting revoked_at."""
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=_apikey_row()))
    revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(keys_repo, "revoke_if_active", revoke)
    resp = client.post("/admin/users/user-1/api-keys/key-1/revoke")
    assert resp.status_code == 204
    revoke.assert_awaited_once()
    assert revoke.await_args.args[1] == "key-1"  # type: ignore[union-attr]
    assert session.commit.await_count == 1


def test_admin_revoke_already_revoked_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=_apikey_row(revoked=True)))
    monkeypatch.setattr(keys_repo, "revoke_if_active", AsyncMock(return_value=False))
    assert client.post("/admin/users/user-1/api-keys/key-1/revoke").status_code == 204
    assert session.commit.await_count == 0


def test_admin_revoke_not_matching_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revoking a key that belongs to a different user returns 404."""
    client, _ = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=_apikey_row(user_id="other-user")))
    revoke = AsyncMock()
    monkeypatch.setattr(keys_repo, "revoke_if_active", revoke)
    resp = client.post("/admin/users/user-1/api-keys/key-1/revoke")
    assert resp.status_code == 404
    revoke.assert_not_awaited()


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
    client, _ = _client_admin(monkeypatch)
    monkeypatch.setattr(users_repo, "set_password", AsyncMock(return_value=True))
    resp = client.post("/admin/users/u1/reset-password")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "temporaryPassword" in body
    assert len(body["temporaryPassword"]) >= 12


def test_admin_reset_password_sets_must_change_password_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user row is updated with a fresh hash and must_change=True."""
    client, session = _client_admin(monkeypatch)
    set_password = AsyncMock(return_value=True)
    monkeypatch.setattr(users_repo, "set_password", set_password)
    resp = client.post("/admin/users/u1/reset-password")
    set_password.assert_awaited_once()
    args: Any = set_password.await_args
    assert args.args[1] == "u1"
    assert args.args[2] not in ("", resp.json()["temporaryPassword"])  # hashed, not plaintext
    assert args.kwargs == {"must_change": True}
    assert session.commit.await_count == 1


def test_admin_reset_password_unknown_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resetting a nonexistent user returns 404."""
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(users_repo, "set_password", AsyncMock(return_value=False))
    resp = client.post("/admin/users/ghost/reset-password")
    assert resp.status_code == 404
    assert session.commit.await_count == 0


def test_member_cannot_reset_password_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin member receives 403 on POST /admin/users/{id}/reset-password."""
    client, _ = _client_member(monkeypatch)
    resp = client.post("/admin/users/u1/reset-password")
    assert resp.status_code == 403
