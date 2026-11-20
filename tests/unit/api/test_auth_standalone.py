"""Tests for /auth/register, /login, /refresh, /disconnect (standalone mode)."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.db.errors import AlreadyExists
from src.db.repos import users as users_repo
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSession, make_user, override_session


def _client_standalone(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
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
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(users_repo, "create", AsyncMock(return_value=make_user()))
    return TestClient(app), session


def test_register_creates_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_standalone(monkeypatch)
    resp = client.post(
        "/auth/register",
        json={
            "email": "alice@example.com",
            "password": "correct-horse-battery-staple",
            "displayName": "Alice",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert "accessToken" in body
    assert "refreshToken" in body
    users_repo.create.assert_awaited_once()  # type: ignore[attr-defined]
    # Ensure we hashed the password (not stored plaintext)
    created = users_repo.create.await_args.kwargs  # type: ignore[attr-defined]
    assert created["password_hash"] != "correct-horse-battery-staple"
    assert created["password_hash"].startswith("$2")
    assert created["display_name"] == "Alice"
    session.commit.assert_awaited_once()


def test_register_duplicate_email_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_standalone(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=make_user()))  # taken
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "any-password-8-chars"},
    )
    assert resp.status_code == 409
    users_repo.create.assert_not_awaited()  # type: ignore[attr-defined]
    session.commit.assert_not_awaited()


def test_register_losing_the_unique_email_race_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_standalone(monkeypatch)
    monkeypatch.setattr(
        users_repo, "create", AsyncMock(side_effect=AlreadyExists("users_email_key"))
    )
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "any-password-8-chars"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "email already registered"
    session.commit.assert_not_awaited()


def test_register_short_password_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_standalone(monkeypatch)
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "short"},  # < 8 chars
    )
    assert resp.status_code == 422


def test_login_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.passwords import hash_password

    client, _ = _client_standalone(monkeypatch)
    monkeypatch.setattr(
        users_repo,
        "get_by_email",
        AsyncMock(return_value=make_user(password_hash=hash_password("right-pass"))),
    )
    resp = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "right-pass"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "accessToken" in body
    assert "refreshToken" in body


def test_login_wrong_password_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.passwords import hash_password

    client, _ = _client_standalone(monkeypatch)
    monkeypatch.setattr(
        users_repo,
        "get_by_email",
        AsyncMock(return_value=make_user(password_hash=hash_password("right-pass"))),
    )
    resp = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "wrong-pass"},
    )
    assert resp.status_code == 401


def test_login_unknown_email_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_standalone(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=None))
    resp = client.post(
        "/auth/login",
        json={"email": "ghost@example.com", "password": "any-password-8+"},
    )
    assert resp.status_code == 401


def test_refresh_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_refresh_token

    client, _ = _client_standalone(monkeypatch)
    refresh = create_refresh_token("u1")
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="u1")))
    resp = client.post("/auth/refresh", json={"refreshToken": refresh})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "accessToken" in body
    assert "refreshToken" in body


def test_refresh_invalid_token_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_standalone(monkeypatch)
    resp = client.post("/auth/refresh", json={"refreshToken": "not.a.jwt"})
    assert resp.status_code == 401


def test_refresh_inactive_user_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_refresh_token

    client, _ = _client_standalone(monkeypatch)
    refresh = create_refresh_token("u1")
    monkeypatch.setattr(
        users_repo, "get_by_id", AsyncMock(return_value=make_user(id="u1", is_active=False))
    )
    resp = client.post("/auth/refresh", json={"refreshToken": refresh})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "account is deactivated"


def test_refresh_must_change_password_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin-forced password reset must cut off refresh immediately —
    otherwise a still-logged-in user could keep refreshing for up to 30 days
    without ever hitting the forced-change gate."""
    from src.security.jwt import create_refresh_token

    client, _ = _client_standalone(monkeypatch)
    refresh = create_refresh_token("u1")
    monkeypatch.setattr(
        users_repo,
        "get_by_id",
        AsyncMock(return_value=make_user(id="u1", must_change_password=True)),
    )
    resp = client.post("/auth/refresh", json={"refreshToken": refresh})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "password change required"


def test_disconnect_returns_204(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_standalone(monkeypatch)
    resp = client.post("/auth/disconnect")
    assert resp.status_code == 204


def test_login_must_change_password_returns_restricted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client_standalone(monkeypatch)
    from src.security.passwords import hash_password

    monkeypatch.setattr(
        users_repo,
        "get_by_email",
        AsyncMock(
            return_value=make_user(
                password_hash=hash_password("temp-pass-123"), must_change_password=True
            )
        ),
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


def test_login_deactivated_user_403(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.passwords import hash_password

    client, _ = _client_standalone(monkeypatch)
    monkeypatch.setattr(
        users_repo,
        "get_by_email",
        AsyncMock(
            return_value=make_user(password_hash=hash_password("right-pass"), is_active=False)
        ),
    )
    resp = client.post("/auth/login", json={"email": "alice@example.com", "password": "right-pass"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "account is deactivated"


def test_refresh_unknown_user_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_refresh_token

    client, _ = _client_standalone(monkeypatch)
    resp = client.post("/auth/refresh", json={"refreshToken": create_refresh_token("u1")})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "user no longer exists"
