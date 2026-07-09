"""Tests for POST /auth/change-password (Account + Workflow Sharing plan, Part A)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.security.rate_limit import RateLimiter


@pytest.fixture(autouse=True)
def _reset_settings_cache_after_test() -> Any:  # pyright: ignore[reportUnusedFunction]
    """Prevent this module's monkeypatched ENVIRONMENT=production from
    leaking into other test files via the process-wide get_settings()
    lru_cache. Without this, whichever test here last triggers a real
    get_settings() read (e.g. via a rate-limited route) leaves a
    "production" Settings instance cached indefinitely, silently breaking
    the ADR-0015 dev-mode fallback for any later test module that expects
    the development default and never calls cache_clear() itself."""
    yield
    from src.config import get_settings

    get_settings.cache_clear()


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

    client, _db = _client(monkeypatch)
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
