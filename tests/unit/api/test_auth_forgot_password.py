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
    client, _db = _client(monkeypatch)
    with patch(
        "src.api.auth_standalone.send_password_reset_email", new_callable=AsyncMock
    ) as mock_send:
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 204, resp.text
    mock_send.assert_awaited_once()
    assert mock_send.await_args is not None
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
    client, _db = _client(monkeypatch)
    with patch(
        "src.api.auth_standalone.send_password_reset_email",
        new_callable=AsyncMock,
        side_effect=RuntimeError("resend is down"),
    ):
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 204, resp.text
