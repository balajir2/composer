"""Unit tests for POST /auth/sso-exchange (Phase 10a)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import users as users_repo
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSession, make_user, override_session


def _client_sso_disabled(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SSO_ENABLED", "false")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    override_session(app)
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def _client_sso_enabled(
    monkeypatch: pytest.MonkeyPatch, *, user_row: Any | None = None
) -> tuple[TestClient, FakeSession]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SSO_ENABLED", "true")
    monkeypatch.setenv("SSO_AZURE_AD_TENANT_ID", "test-tenant")
    monkeypatch.setenv("SSO_AZURE_AD_EXPECTED_AUDIENCE", "api://composer")
    from src.config import get_settings

    get_settings.cache_clear()

    # Mock the verifier so the test doesn't need real Azure JWKS.
    async def _fake_verify(token: str, *, tenant_id: str, expected_audience: str) -> dict[str, Any]:
        if token == "bad":
            from src.security.auth import AuthError

            raise AuthError("bad token")
        return {"email": "alice@example.com", "name": "Alice"}

    # The endpoint imports verify_azure_jwt lazily; patch at call-site module.
    monkeypatch.setattr("src.security.sso_azure.verify_azure_jwt", _fake_verify)

    app = create_app()
    session = override_session(app)
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=user_row))
    monkeypatch.setattr(
        users_repo, "create", AsyncMock(return_value=make_user(id="u-new", password_hash=None))
    )
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), session


def test_sso_exchange_disabled_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_sso_disabled(monkeypatch)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "x"})
    assert resp.status_code == 400


def test_sso_exchange_autoprovisions_new_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_sso_enabled(monkeypatch, user_row=None)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "valid"})
    assert resp.status_code == 200
    body = resp.json()
    assert "accessToken" in body
    assert "refreshToken" in body
    users_repo.create.assert_awaited_once()  # type: ignore[attr-defined]
    create_kwargs = users_repo.create.await_args.kwargs  # type: ignore[attr-defined]
    assert create_kwargs["email"] == "alice@example.com"
    assert create_kwargs["password_hash"] is None
    assert create_kwargs["display_name"] == "Alice"
    session.commit.assert_awaited_once()


def test_sso_exchange_existing_user_not_recreated(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = make_user(id="u-existing", password_hash=None)
    client, _ = _client_sso_enabled(monkeypatch, user_row=existing)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "valid"})
    assert resp.status_code == 200
    users_repo.create.assert_not_awaited()  # type: ignore[attr-defined]


def test_sso_exchange_invalid_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_sso_enabled(monkeypatch)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "bad"})
    assert resp.status_code == 401


def test_sso_exchange_inactive_user_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    inactive = make_user(id="u-inactive", password_hash=None, is_active=False)
    client, _ = _client_sso_enabled(monkeypatch, user_row=inactive)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "valid"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "account is deactivated"
    users_repo.create.assert_not_awaited()  # type: ignore[attr-defined]
