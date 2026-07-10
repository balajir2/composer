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

    client, _db = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 204, resp.text


def test_reset_password_rejects_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_access_token

    client, _db = _client(monkeypatch)
    token = create_access_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 400


def test_reset_password_rejects_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_refresh_token

    client, _db = _client(monkeypatch)
    token = create_refresh_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 400


def test_reset_password_rejects_malformed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _db = _client(monkeypatch)
    resp = client.post(
        "/auth/reset-password",
        json={"token": "not.a.jwt", "newPassword": "brand-new-password-99"},
    )
    assert resp.status_code == 400


def test_reset_password_short_new_password_422(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_password_change_token

    client, _db = _client(monkeypatch)
    token = create_password_change_token("u1")
    resp = client.post(
        "/auth/reset-password",
        json={"token": token, "newPassword": "short"},
    )
    assert resp.status_code == 422
