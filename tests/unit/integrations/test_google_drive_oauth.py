"""Tests for Google Drive OAuth primitives (standard 3-legged OAuth2, no
RFC 8707 resource param — that was MCP/Highspot-specific)."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _set_oauth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-abc")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-xyz")
    from src.config import get_settings

    get_settings.cache_clear()


def test_build_authorize_url_includes_expected_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import build_authorize_url

    url = build_authorize_url(
        "user1", "https://api.example.com/cloud-storage/google-drive/callback"
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-abc" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file" in url
    assert "access_type=offline" in url
    assert "state=" in url


def test_consume_state_round_trips_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    from src.integrations.google_drive.oauth import build_state, consume_state

    state = build_state("user1")
    assert consume_state(state) == "user1"


def test_consume_state_rejects_expired_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    import json

    from src.integrations.google_drive.oauth import InvalidStateError, consume_state
    from src.security.encryption import encrypt

    expired_payload = json.dumps(
        {"user_id": "user1", "exp": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
    )
    with pytest.raises(InvalidStateError, match="expired"):
        consume_state(encrypt(expired_payload))


async def test_exchange_code_for_tokens_fetches_email(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import exchange_code_for_tokens

    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        json={
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "expires_in": 3600,
            "scope": "drive.file",
        },
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/oauth2/v2/userinfo",
        method="GET",
        json={"email": "designer@example.com"},
    )

    result = await exchange_code_for_tokens("auth-code", "https://api.example.com/callback")
    assert result["access_token"] == "at-1"
    assert result["email"] == "designer@example.com"


async def test_get_valid_drive_access_token_refreshes_when_near_expiry(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import get_valid_drive_access_token
    from src.security.encryption import encrypt

    connection = SimpleNamespace(
        id="conn1",
        encryptedAccessToken=encrypt("old-at"),
        encryptedRefreshToken=encrypt("rt-1"),
        expiresAt=datetime.now(UTC) - timedelta(minutes=1),
    )
    db = MagicMock()
    db.cloudstorageconnection = MagicMock()
    db.cloudstorageconnection.find_unique = AsyncMock(return_value=connection)
    db.cloudstorageconnection.update = AsyncMock(
        return_value=SimpleNamespace(id="conn1", encryptedAccessToken=encrypt("new-at"))
    )
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )

    token = await get_valid_drive_access_token("conn1", db)
    assert token == "new-at"
    db.cloudstorageconnection.update.assert_awaited_once()
