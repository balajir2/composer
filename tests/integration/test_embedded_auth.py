"""Integration — embedded-mode JWT validation.

Builds a fresh FastAPI app with COMPOSER_DEPLOYMENT_MODE=embedded +
IEP_SHARED_SECRET, then hits /auth/me with:
  - IEP-signed JWT (correct issuer)  → 200 + claims
  - IEP-signed JWT (wrong issuer)    → 401
  - No Authorization header          → 401 (ENVIRONMENT=production; no dev fallback)

Does not use the conftest `client` / `app` fixtures — those are
standalone-by-default.  Instead constructs a fresh app per test and
uses httpx.AsyncClient over ASGITransport.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]

pytestmark = pytest.mark.integration


_IEP_SECRET = "iep-test-secret-long-enough-for-hs256"
_IEP_ISSUER = "https://iep-test.composer"


def _mk_iep_token(sub: str, *, iss: str = _IEP_ISSUER, ttl_seconds: int = 3600) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": iss,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jose_jwt.encode(payload, _IEP_SECRET, algorithm="HS256")


async def _embedded_client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_SHARED_SECRET", _IEP_SECRET)
    monkeypatch.setenv("IEP_JWT_ISSUER", _IEP_ISSUER)
    from src.config import get_settings

    get_settings.cache_clear()

    from src.main import create_app

    app = create_app()
    # /auth/me in embedded mode does NOT query the User table, so a
    # MagicMock db satisfies any code paths that touch app.state.db.
    app.state.db = MagicMock()
    app.state.checkpointer = MagicMock()

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_embedded_accepts_correct_iep_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _embedded_client(monkeypatch)
    try:
        token = _mk_iep_token(sub="iep-user-1")
        resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "iep-user-1"
        assert "claims" in body
        assert body["claims"]["sub"] == "iep-user-1"
        assert body["claims"]["iss"] == _IEP_ISSUER
    finally:
        await client.aclose()


async def test_embedded_rejects_wrong_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _embedded_client(monkeypatch)
    try:
        token = _mk_iep_token(sub="x", iss="https://evil.example")
        resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
    finally:
        await client.aclose()


async def test_embedded_rejects_missing_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _embedded_client(monkeypatch)
    try:
        resp = await client.get("/auth/me")
        assert resp.status_code == 401
    finally:
        await client.aclose()


async def test_embedded_rejects_bad_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _embedded_client(monkeypatch)
    try:
        # Token signed with wrong secret
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": "x",
            "iss": _IEP_ISSUER,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }
        bad = jose_jwt.encode(payload, "wrong-secret", algorithm="HS256")
        resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401
    finally:
        await client.aclose()
