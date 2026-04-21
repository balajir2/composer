"""Regression — OAB highspot-oauth-lifecycle.spec.ts.

OAB source: D:/GitHub/open-agent-builder/tests/highspot-oauth-lifecycle.spec.ts
  Asserts the token-exchange request body includes `resource` (OAB lost
  a multi-day debugging session to this missing param).

This port validates the same invariant in Composer's oauth.py. It does
NOT require a real Highspot instance — it mocks the token endpoint and
asserts the POST body.
"""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.mcp.oauth import exchange_code_for_tokens, refresh_token
from src.security.encryption import encrypt

pytestmark = pytest.mark.integration


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings
    get_settings.cache_clear()


def _highspot_server() -> Any:
    return SimpleNamespace(
        id="srv-hs",
        userId="dev",
        url="https://mcp.highspot.com/mcp",
        oauthConfig={
            "authorizeUrl": "https://mcp.highspot.com/oauth/authorize",
            "tokenUrl": "https://mcp.highspot.com/oauth/token",
            "clientId": "hs-client",
            "clientSecret": "hs-secret",
            "scopes": ["read"],
        },
    )


async def test_oab_regression_token_exchange_includes_resource(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """OAB-caught: token exchange MUST carry resource=https://mcp.highspot.com.

    Without this, Highspot responds with 'resource mismatch' and auth fails.
    """
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://mcp.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
    )

    state_row = SimpleNamespace(
        id="st1",
        mcpServerId="srv-hs",
        userId="dev",
        state="s1",
        codeVerifier="cv",
        redirectUri="http://localhost/cb",
        scope="read",
        expiresAt=datetime.now(UTC) + timedelta(minutes=4),
    )
    db = MagicMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.find_unique = AsyncMock(return_value=state_row)
    db.mcpoauthstate.delete = AsyncMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.upsert = AsyncMock(return_value=SimpleNamespace(id="tok1"))

    await exchange_code_for_tokens(_highspot_server(), code="c", state="s1", db=db)

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fmcp.highspot.com" in body, (
        f"Regression: token exchange must include resource param. Body was: {body}"
    )


async def test_oab_regression_refresh_includes_resource(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """OAB-caught: refresh MUST carry resource too — Highspot enforces it on refresh."""
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://mcp.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )

    token_row = SimpleNamespace(
        id="tok1",
        mcpServerId="srv-hs",
        userId="dev",
        encryptedAccessToken=encrypt("old-at"),
        encryptedRefreshToken=encrypt("rt-1"),
        expiresAt=datetime.now(UTC) - timedelta(minutes=1),
        scope="read",
        tokenType="Bearer",
    )
    db = MagicMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.update = AsyncMock()

    await refresh_token(_highspot_server(), token_row, db)

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fmcp.highspot.com" in body, (
        f"Regression: refresh must include resource. Body was: {body}"
    )
