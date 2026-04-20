"""Tests for refresh_token — third resource-param assertion."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.mcp.oauth import TokenRefreshError, refresh_token
from src.security.encryption import encrypt


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server() -> Any:
    return SimpleNamespace(
        id="srv1",
        userId="owner",
        url="https://api.highspot.com/mcp",
        oauthConfig={
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": "plain-secret",
        },
    )


def _token_row(monkeypatch: pytest.MonkeyPatch) -> Any:
    _set_enc_key(monkeypatch)
    return SimpleNamespace(
        id="tok1",
        mcpServerId="srv1",
        userId="user1",
        encryptedAccessToken=encrypt("old-access"),
        encryptedRefreshToken=encrypt("refresh-123"),
        expiresAt=datetime.now(UTC) - timedelta(minutes=1),
        scope="read",
        tokenType="Bearer",
    )


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.update = AsyncMock(return_value=SimpleNamespace(id="tok1"))
    return db


async def test_refresh_sends_resource_param(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """FIX #1 (third of four): refresh POST must include resource param."""
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )
    await refresh_token(_server(), tok, db)
    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fapi.highspot.com" in body
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh-123" in body


async def test_refresh_updates_token_row(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
    )
    await refresh_token(_server(), tok, db)
    db.mcpoauthtoken.update.assert_awaited_once()
    call = db.mcpoauthtoken.update.await_args
    data = call.kwargs["data"]
    assert data["encryptedAccessToken"] != "new-at"  # encrypted
    assert data["encryptedRefreshToken"] != "new-rt"
    assert data["expiresAt"] > datetime.now(UTC) + timedelta(minutes=50)


async def test_refresh_keeps_old_refresh_token_if_idp_omits(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Some IdPs don't rotate the refresh token on refresh. Keep the existing one."""
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},  # no refresh_token
    )
    await refresh_token(_server(), tok, db)
    data = db.mcpoauthtoken.update.await_args.kwargs["data"]
    # Key is present and equal to the original (unchanged)
    assert data["encryptedRefreshToken"] == tok.encryptedRefreshToken


async def test_refresh_raises_on_idp_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    tok = _token_row(monkeypatch)
    db = _mock_db()
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        status_code=401,
        json={"error": "invalid_grant"},
    )
    with pytest.raises(TokenRefreshError, match="401"):
        await refresh_token(_server(), tok, db)
