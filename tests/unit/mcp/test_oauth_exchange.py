"""Tests for exchange_code_for_tokens — including the resource-param assertion."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.mcp.oauth import InvalidStateError, TokenExchangeError, exchange_code_for_tokens


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "owner",
        "url": "https://api.highspot.com/mcp",
        "oauthConfig": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": "plain-secret-for-test",
            "scopes": ["read"],
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _state_row(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "st1",
        "mcpServerId": "srv1",
        "userId": "user1",
        "state": "abc",
        "codeVerifier": "ver123",
        "redirectUri": "http://localhost:3000/cb",
        "scope": "read",
        "expiresAt": datetime.now(UTC) + timedelta(minutes=4),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_db(state_row: Any | None) -> MagicMock:
    db = MagicMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.find_unique = AsyncMock(return_value=state_row)
    db.mcpoauthstate.delete = AsyncMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.upsert = AsyncMock(return_value=SimpleNamespace(id="tok1"))
    return db


async def test_exchange_sends_resource_param(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """FIX #1 (second of four): token-exchange POST must include resource param."""
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
    )
    db = _mock_db(_state_row())
    await exchange_code_for_tokens(_server(), code="code-1", state="abc", db=db)

    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    # Content-Type is form-urlencoded
    assert "resource=https%3A%2F%2Fapi.highspot.com" in body
    assert "grant_type=authorization_code" in body
    assert "code=code-1" in body
    assert "code_verifier=ver123" in body


async def test_exchange_stores_encrypted_tokens(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "plain-at", "refresh_token": "plain-rt", "expires_in": 3600},
    )
    db = _mock_db(_state_row())
    await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)
    db.mcpoauthtoken.upsert.assert_awaited_once()
    call = db.mcpoauthtoken.upsert.await_args
    created = call.kwargs["data"]["create"]
    assert created["encryptedAccessToken"] != "plain-at"
    assert created["encryptedAccessToken"]
    assert created["encryptedRefreshToken"] != "plain-rt"
    assert created["mcpServerId"] == "srv1"
    assert created["userId"] == "user1"


async def test_exchange_deletes_state_row_once(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "expires_in": 3600},
    )
    db = _mock_db(_state_row())
    await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)
    db.mcpoauthstate.delete.assert_awaited_once()


async def test_exchange_decrypts_encrypted_client_secret(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """P0-5: oauthConfig.clientSecret is now encrypted at rest by the
    mcp-servers API — the exchange must decrypt it before sending, or the
    IdP's token endpoint receives ciphertext as client_secret."""
    from src.security.encryption import encrypt_marked

    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "expires_in": 3600},
    )
    server = _server(
        oauthConfig={
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": encrypt_marked("real-client-secret"),
            "scopes": ["read"],
        }
    )
    db = _mock_db(_state_row())
    await exchange_code_for_tokens(server, code="c", state="abc", db=db)

    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    assert "client_secret=real-client-secret" in body


async def test_exchange_raises_for_unknown_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    db = _mock_db(None)  # state not found
    with pytest.raises(InvalidStateError, match="state"):
        await exchange_code_for_tokens(_server(), code="c", state="missing", db=db)


async def test_exchange_raises_for_expired_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    expired = _state_row(expiresAt=datetime.now(UTC) - timedelta(minutes=1))
    db = _mock_db(expired)
    with pytest.raises(InvalidStateError, match="expired"):
        await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)


async def test_exchange_raises_on_idp_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        status_code=400,
        json={"error": "invalid_grant"},
    )
    db = _mock_db(_state_row())
    with pytest.raises(TokenExchangeError, match="400"):
        await exchange_code_for_tokens(_server(), code="c", state="abc", db=db)
