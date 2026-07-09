"""Tests for /auth/register, /login, /refresh, /disconnect (standalone mode)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.security.rate_limit import RateLimiter


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
    db.user.find_unique = AsyncMock(return_value=None)
    db.user.create = AsyncMock(return_value=_user_row())
    app.state.db = db
    app.state.rate_limiter = RateLimiter()
    # get_db dependency reads from request.app.state.db — set above
    return TestClient(app), db


def test_register_creates_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
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
    db.user.create.assert_awaited_once()
    # Ensure we hashed the password (not stored plaintext)
    call = db.user.create.await_args
    created = call.kwargs["data"]
    assert created["passwordHash"] != "correct-horse-battery-staple"
    assert created["passwordHash"].startswith("$2")


def test_register_duplicate_email_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_standalone(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row())  # email taken
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "any-password-8-chars"},
    )
    assert resp.status_code == 409


def test_register_short_password_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_standalone(monkeypatch)
    resp = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "short"},  # < 8 chars
    )
    assert resp.status_code == 422


def test_login_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.passwords import hash_password

    client, db = _client_standalone(monkeypatch)
    db.user.find_unique = AsyncMock(
        return_value=_user_row(passwordHash=hash_password("right-pass"))
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

    client, db = _client_standalone(monkeypatch)
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
        json={"email": "ghost@example.com", "password": "any-password-8+"},
    )
    assert resp.status_code == 401


def test_refresh_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_refresh_token

    client, db = _client_standalone(monkeypatch)
    refresh = create_refresh_token("u1")
    db.user.find_unique = AsyncMock(return_value=_user_row(id="u1"))
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

    client, db = _client_standalone(monkeypatch)
    refresh = create_refresh_token("u1")
    db.user.find_unique = AsyncMock(return_value=_user_row(id="u1", isActive=False))
    resp = client.post("/auth/refresh", json={"refreshToken": refresh})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "account is deactivated"


def test_disconnect_returns_204(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_standalone(monkeypatch)
    resp = client.post("/auth/disconnect")
    assert resp.status_code == 204


def test_login_must_change_password_returns_restricted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client_standalone(monkeypatch)
    from src.security.passwords import hash_password

    db.user.find_unique = AsyncMock(
        return_value=_user_row(passwordHash=hash_password("temp-pass-123"), mustChangePassword=True)
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
