"""Tests for Google Drive OAuth primitives (standard 3-legged OAuth2, no
RFC 8707 resource param — that was MCP/Highspot-specific)."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
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
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.readonly" in url
    # drive.metadata must be requested alongside drive.readonly, or
    # move_file()'s appProperties PATCH (the claim marker) gets rejected --
    # drive.readonly is read-only. userinfo.email must be requested too, or
    # the userinfo lookup in exchange_code_for_tokens() gets a 401 from
    # Google even though the token exchange itself succeeded. Both gaps
    # were real bugs hit in production (2026-07-18): drive.file alone
    # looked sufficient but its Picker-granted access didn't survive to
    # server-side polling minutes later.
    assert "drive.metadata" in url
    assert "userinfo.email" in url
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
            "scope": "drive.readonly drive.metadata",
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
    # Google's refresh response here omits refresh_token (no rotation) —
    # the update must NOT touch encryptedRefreshToken, or a regression
    # could silently overwrite the stored refresh token with garbage.
    _, kwargs = db.cloudstorageconnection.update.call_args
    assert "encryptedRefreshToken" not in kwargs["data"]


async def test_get_valid_drive_access_token_persists_rotated_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Google's refresh response may include a new refresh_token (rotation);
    it must be persisted, not silently dropped."""
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import get_valid_drive_access_token
    from src.security.encryption import decrypt, encrypt

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
        json={"access_token": "new-at", "refresh_token": "rt-2", "expires_in": 3600},
    )

    await get_valid_drive_access_token("conn1", db)

    db.cloudstorageconnection.update.assert_awaited_once()
    _, kwargs = db.cloudstorageconnection.update.call_args
    assert "encryptedRefreshToken" in kwargs["data"]
    assert decrypt(kwargs["data"]["encryptedRefreshToken"]) == "rt-2"


async def test_exchange_code_for_tokens_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import TokenExchangeError, exchange_code_for_tokens

    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        status_code=400,
        json={"error": "invalid_grant"},
    )

    with pytest.raises(TokenExchangeError):
        await exchange_code_for_tokens("bad-code", "https://api.example.com/callback")


async def test_refresh_access_token_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import TokenRefreshError, refresh_access_token

    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        status_code=400,
        json={"error": "invalid_grant"},
    )

    with pytest.raises(TokenRefreshError):
        await refresh_access_token("stale-refresh-token")


async def test_exchange_code_for_tokens_wraps_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """A genuine transport failure (connection refused, DNS, timeout — not
    an HTTP error status) must surface as the module's typed exception, not
    a raw httpx.HTTPError, so callers catching GoogleDriveOAuthError don't
    miss it."""
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import (
        GOOGLE_TOKEN_URL,
        TokenExchangeError,
        exchange_code_for_tokens,
    )

    httpx_mock.add_exception(
        method="POST",
        url=GOOGLE_TOKEN_URL,
        exception=httpx.ConnectError("connection refused"),
    )

    with pytest.raises(TokenExchangeError):
        await exchange_code_for_tokens("auth-code", "https://api.example.com/callback")


async def test_refresh_access_token_wraps_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import (
        GOOGLE_TOKEN_URL,
        TokenRefreshError,
        refresh_access_token,
    )

    httpx_mock.add_exception(
        method="POST",
        url=GOOGLE_TOKEN_URL,
        exception=httpx.ConnectError("connection refused"),
    )

    with pytest.raises(TokenRefreshError):
        await refresh_access_token("stale-refresh-token")


async def test_get_valid_drive_access_token_raises_on_refresh_http_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import TokenRefreshError, get_valid_drive_access_token
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
    db.cloudstorageconnection.update = AsyncMock()
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        status_code=500,
        json={"error": "server_error"},
    )

    with pytest.raises(TokenRefreshError):
        await get_valid_drive_access_token("conn1", db)
    db.cloudstorageconnection.update.assert_not_awaited()


async def test_get_valid_drive_access_token_raises_when_connection_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import (
        DriveConnectionMissingError,
        get_valid_drive_access_token,
    )

    db = MagicMock()
    db.cloudstorageconnection = MagicMock()
    db.cloudstorageconnection.find_unique = AsyncMock(return_value=None)

    with pytest.raises(DriveConnectionMissingError):
        await get_valid_drive_access_token("missing-conn", db)


async def test_get_valid_drive_access_token_raises_when_expired_with_no_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import (
        DriveTokenExpiredError,
        get_valid_drive_access_token,
    )
    from src.security.encryption import encrypt

    connection = SimpleNamespace(
        id="conn1",
        encryptedAccessToken=encrypt("old-at"),
        encryptedRefreshToken=None,
        expiresAt=datetime.now(UTC) - timedelta(minutes=1),
    )
    db = MagicMock()
    db.cloudstorageconnection = MagicMock()
    db.cloudstorageconnection.find_unique = AsyncMock(return_value=connection)

    with pytest.raises(DriveTokenExpiredError):
        await get_valid_drive_access_token("conn1", db)


def test_consume_state_rejects_garbled_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    from src.integrations.google_drive.oauth import InvalidStateError, consume_state

    with pytest.raises(InvalidStateError):
        consume_state("not-a-valid-encrypted-state-value")


def test_consume_state_propagates_missing_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing/misconfigured ENCRYPTION_KEY is a server misconfiguration,
    not a tampered state — it must not be swallowed into InvalidStateError.

    Set (rather than delete) the env var to an empty string: Settings also
    reads from a `.env` file, so deleting the process env var alone would
    leave a real dev key in effect via that fallback source.
    """
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    from src.config import get_settings

    get_settings.cache_clear()
    from src.integrations.google_drive.oauth import consume_state
    from src.security.encryption import EncryptionKeyMissingError

    with pytest.raises(EncryptionKeyMissingError):
        consume_state("anything")
