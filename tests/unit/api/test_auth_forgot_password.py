"""Tests for POST /auth/forgot-password (self-service password reset plan)."""

from unittest.mock import AsyncMock, patch

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


def test_forgot_password_existing_user_sends_email_and_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(monkeypatch)
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
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=None))
    with patch(
        "src.api.auth_standalone.send_password_reset_email", new_callable=AsyncMock
    ) as mock_send:
        resp = client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
    assert resp.status_code == 204, resp.text
    mock_send.assert_not_awaited()


def test_forgot_password_sso_only_account_returns_204_no_email_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(
        users_repo, "get_by_email", AsyncMock(return_value=make_user(password_hash=None))
    )
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
    client, _ = _client(monkeypatch)
    with patch(
        "src.api.auth_standalone.send_password_reset_email",
        new_callable=AsyncMock,
        side_effect=RuntimeError("resend is down"),
    ):
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 204, resp.text


def test_forgot_password_releases_connection_before_sending_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lookup happens, then rollback frees the pooled connection, then the slow email."""
    client, session = _client(monkeypatch)
    order: list[str] = []

    async def _lookup(*_a: object, **_k: object) -> object:
        order.append("lookup")
        return _pw_user()

    async def _rollback() -> None:
        order.append("rollback")

    async def _send(**_k: object) -> None:
        order.append("send")

    monkeypatch.setattr(users_repo, "get_by_email", _lookup)
    session.rollback.side_effect = _rollback
    with patch("src.api.auth_standalone.send_password_reset_email", side_effect=_send):
        resp = client.post("/auth/forgot-password", json={"email": "alice@example.com"})
    assert resp.status_code == 204
    assert order == ["lookup", "rollback", "send"]
