"""Tests for /auth/me (both modes)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]


def _app_with_router(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "standalone",
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", mode)
    monkeypatch.setenv("ENVIRONMENT", "production")
    if mode == "embedded":
        monkeypatch.setenv("IEP_SHARED_SECRET", "iep-test-secret-32-chars-min-here-please")
        monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    from src.config import get_settings

    get_settings.cache_clear()

    from src.api.auth_common import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="u1",
            email="alice@example.com",
            displayName="Alice",
            role="member",
        )
    )
    app.state.db = db
    return TestClient(app), db


def test_me_standalone_returns_user_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_access_token

    client, _db = _app_with_router(monkeypatch, mode="standalone")
    token = create_access_token("u1")
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "u1"
    assert body["email"] == "alice@example.com"
    assert body["displayName"] == "Alice"
    assert body["role"] == "member"


def test_me_standalone_user_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_access_token

    client, db = _app_with_router(monkeypatch, mode="standalone")
    db.user.find_unique = AsyncMock(return_value=None)
    token = create_access_token("ghost")
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_me_embedded_returns_jwt_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _app_with_router(monkeypatch, mode="embedded")
    # Build a manual JWT with iep_shared_secret + correct iss
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": "external-user",
        "iss": "https://iep.test",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    token = jose_jwt.encode(payload, "iep-test-secret-32-chars-min-here-please", algorithm="HS256")
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "external-user"
    assert "claims" in body
    # The claims dict should contain at least sub + iss
    assert body["claims"]["sub"] == "external-user"
    assert body["claims"]["iss"] == "https://iep.test"


def test_me_missing_auth_401_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _app_with_router(monkeypatch, mode="standalone")
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_dev_mode_fallback_returns_dev_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """In development, missing auth header → user_id='dev' → 404 (no User row for 'dev')."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    from src.config import get_settings

    get_settings.cache_clear()

    from src.api.auth_common import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    db = MagicMock()
    db.user = MagicMock()
    # 'dev' fallback user doesn't exist in the User table → 404
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db

    client = TestClient(app)
    resp = client.get("/auth/me")  # no Authorization header
    # dev fallback → user_id='dev' → /auth/me looks up User where id='dev'
    # → 404 since there's no such row.  Auth middleware fell through cleanly.
    assert resp.status_code == 404
    db.user.find_unique.assert_awaited_once_with(where={"id": "dev"})
