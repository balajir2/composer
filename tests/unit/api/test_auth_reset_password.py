"""Tests for POST /auth/reset-password (self-service password reset plan)."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import users as users_repo
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSession, make_user, override_session


def _pw_user(**overrides: object) -> object:
    from src.security.passwords import hash_password

    return make_user(password_hash=hash_password("old-password-8"), **overrides)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
    from fastapi import FastAPI

    from src.api.auth_standalone import router as auth_router

    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(auth_router)
    session = override_session(app)
    app.state.rate_limiter = RateLimiter()
    user = _pw_user()
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(users_repo, "set_password", AsyncMock(return_value=True))
    return TestClient(app), session


def test_reset_password_with_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_password_change_token

    client, session = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 204, resp.text
    users_repo.set_password.assert_awaited_once()  # type: ignore[attr-defined]
    args = users_repo.set_password.await_args  # type: ignore[attr-defined]
    assert args.args[2] != "brand-new-password-99" and args.args[2].startswith("$2")
    assert args.kwargs == {"must_change": False}
    session.commit.assert_awaited_once()


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


def test_reset_password_rejects_deactivated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_password_change_token

    client, session = _client(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=_pw_user(is_active=False)))
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 400
    users_repo.set_password.assert_not_awaited()  # type: ignore[attr-defined]
    session.commit.assert_not_awaited()


def test_reset_password_short_new_password_422(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_password_change_token

    client, _ = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "short"},
    )
    assert resp.status_code == 422
