"""Unit tests for POST /auth/sso-exchange (Phase 10a)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _client_sso_disabled(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SSO_ENABLED", "false")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def _client_sso_enabled(
    monkeypatch: pytest.MonkeyPatch, *, user_row: Any | None = None
) -> tuple[TestClient, Any]:
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
    db = MagicMock()
    db.user = MagicMock()
    created_row = SimpleNamespace(
        id="u-new",
        email="alice@example.com",
        displayName="Alice",
        passwordHash=None,
        role=SimpleNamespace(value="member"),
    )
    db.user.find_unique = AsyncMock(return_value=user_row)
    db.user.create = AsyncMock(return_value=created_row)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_sso_exchange_disabled_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_sso_disabled(monkeypatch)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "x"})
    assert resp.status_code == 400


def test_sso_exchange_autoprovisions_new_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_sso_enabled(monkeypatch, user_row=None)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "valid"})
    assert resp.status_code == 200
    body = resp.json()
    assert "accessToken" in body
    assert "refreshToken" in body
    db.user.create.assert_awaited_once()
    create_kwargs = db.user.create.await_args.kwargs["data"]
    assert create_kwargs["email"] == "alice@example.com"
    assert create_kwargs["passwordHash"] is None


def test_sso_exchange_existing_user_not_recreated(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = SimpleNamespace(
        id="u-existing",
        email="alice@example.com",
        displayName="Alice",
        passwordHash=None,
        role=SimpleNamespace(value="member"),
    )
    client, db = _client_sso_enabled(monkeypatch, user_row=existing)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "valid"})
    assert resp.status_code == 200
    db.user.create.assert_not_awaited()


def test_sso_exchange_invalid_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_sso_enabled(monkeypatch)
    resp = client.post("/auth/sso-exchange", json={"azureToken": "bad"})
    assert resp.status_code == 401
