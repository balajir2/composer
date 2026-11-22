"""Tests for Google Drive OAuth primitives (standard 3-legged OAuth2, no
RFC 8707 resource param — that was MCP/Highspot-specific)."""

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.db import locks
from src.db.repos import cloud_storage as cloud_storage_repo
from src.db.repos.cloud_storage import CloudStorageConnectionRecord
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session


def _connection(**overrides: Any) -> CloudStorageConnectionRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "conn1",
        "user_id": "user1",
        "provider": "google-drive",
        "account_email": "a@example.com",
        "encrypted_access_token": "",
        "encrypted_refresh_token": None,
        "expires_at": None,
        "scope": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return CloudStorageConnectionRecord(**base)


def _wire_repo(
    monkeypatch: pytest.MonkeyPatch,
    connection: CloudStorageConnectionRecord | None,
    updated: CloudStorageConnectionRecord | None = None,
    lock_error: Exception | None = None,
) -> tuple[RecordingSessionmaker, AsyncMock, list[str]]:
    """Wire the repo and the advisory lock; ``events`` logs db and lock calls in order."""
    events: list[str] = []

    async def _get(_session: Any, _connection_id: str) -> CloudStorageConnectionRecord | None:
        events.append("db:get")
        return connection

    async def _update(
        _session: Any, _connection_id: str, _changes: Any
    ) -> CloudStorageConnectionRecord | None:
        events.append("db:update")
        return updated

    async def _lock(_session: Any, _key: str, **_kw: Any) -> None:
        events.append("db:lock")
        if lock_error is not None:
            raise lock_error

    monkeypatch.setattr(locks, "acquire_xact_lock", _lock)
    update = AsyncMock(side_effect=_update)
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(side_effect=_get))
    monkeypatch.setattr(cloud_storage_repo, "update_tokens", update)
    return RecordingSessionmaker(events), update, events


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


async def test_get_valid_drive_access_token_raises_when_connection_vanishes_mid_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row is deleted between the read and the post-refresh update: the helper must not
    fall back to the stale, expired access token (Prisma's update raised here)."""
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    import src.integrations.google_drive.oauth as drive_oauth
    from src.security.encryption import encrypt

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, _update, _events = _wire_repo(monkeypatch, connection, updated=None)
    monkeypatch.setattr(
        drive_oauth,
        "refresh_access_token",
        AsyncMock(return_value={"access_token": "new-at", "expires_in": 3600}),
    )

    with pytest.raises(drive_oauth.DriveConnectionMissingError) as exc_info:
        await drive_oauth.get_valid_drive_access_token("conn1", sm)  # type: ignore[arg-type]
    assert str(exc_info.value) == "No CloudStorageConnection found for id 'conn1'."


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

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, update, events = _wire_repo(
        monkeypatch, connection, _connection(encrypted_access_token=encrypt("new-at"))
    )
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )

    token = await get_valid_drive_access_token("conn1", sm)  # type: ignore[arg-type]
    assert token == "new-at"
    update.assert_awaited_once()
    # Google's refresh response here omits refresh_token (no rotation) —
    # the update must NOT touch the refresh token, or a regression
    # could silently overwrite the stored refresh token with garbage.
    assert update.await_args is not None
    changes = update.await_args.args[2]
    assert "encrypted_refresh_token" not in changes
    assert set(changes) == {"encrypted_access_token", "expires_at"}
    assert_no_network_inside_session(events)


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

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, update, _events = _wire_repo(
        monkeypatch, connection, _connection(encrypted_access_token=encrypt("new-at"))
    )
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        json={"access_token": "new-at", "refresh_token": "rt-2", "expires_in": 3600},
    )

    await get_valid_drive_access_token("conn1", sm)  # type: ignore[arg-type]

    update.assert_awaited_once()
    assert update.await_args is not None
    changes = update.await_args.args[2]
    assert "encrypted_refresh_token" in changes
    assert decrypt(changes["encrypted_refresh_token"]) == "rt-2"


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

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, update, _events = _wire_repo(monkeypatch, connection)
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        method="POST",
        status_code=500,
        json={"error": "server_error"},
    )

    with pytest.raises(TokenRefreshError):
        await get_valid_drive_access_token("conn1", sm)  # type: ignore[arg-type]
    update.assert_not_awaited()


async def test_get_valid_drive_access_token_raises_when_connection_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    from src.integrations.google_drive.oauth import (
        DriveConnectionMissingError,
        get_valid_drive_access_token,
    )

    sm, _update, _events = _wire_repo(monkeypatch, None)

    with pytest.raises(DriveConnectionMissingError):
        await get_valid_drive_access_token("missing-conn", sm)  # type: ignore[arg-type]


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

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=None,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, _update, _events = _wire_repo(monkeypatch, connection)

    with pytest.raises(DriveTokenExpiredError):
        await get_valid_drive_access_token("conn1", sm)  # type: ignore[arg-type]


async def test_refresh_holds_one_session_under_the_lock_and_posts_only_after_lock_and_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast-path read closes before anything else; the refresh then runs in ONE committed
    session: lock, re-read, the Google POST (the only network call allowed inside a session),
    write (documented exception to the short-session rule)."""
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    import src.integrations.google_drive.oauth as drive_oauth
    from src.security.encryption import encrypt

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, _update, events = _wire_repo(
        monkeypatch, connection, _connection(encrypted_access_token=encrypt("new-at"))
    )

    async def _refresh(_rt: str) -> dict[str, Any]:
        events.append("net:refresh")
        return {"access_token": "new-at", "expires_in": 3600}

    monkeypatch.setattr(drive_oauth, "refresh_access_token", _refresh)

    assert await drive_oauth.get_valid_drive_access_token("conn1", sm) == "new-at"  # type: ignore[arg-type]
    assert events == [
        "session:enter",
        "db:get",
        "session:exit",
        "session:enter",
        "db:lock",
        "db:get",
        "net:refresh",
        "db:update",
        "session:exit",
    ]
    assert_no_network_inside_session(events, allowed_inside=frozenset({"net:refresh"}))
    sm.session.commit.assert_awaited_once()


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


async def test_refresh_skips_the_post_when_another_caller_just_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fast path sees an expired token; the re-read under the lock sees a fresh one."""
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    import src.integrations.google_drive.oauth as drive_oauth
    from src.security.encryption import encrypt

    stale = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    fresh = _connection(
        encrypted_access_token=encrypt("fresh-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sm, update, events = _wire_repo(monkeypatch, stale)
    reads = iter([stale, fresh])

    async def _get(_session: Any, _id: str) -> Any:
        events.append("db:get")
        return next(reads)

    monkeypatch.setattr(cloud_storage_repo, "get", _get)
    post = AsyncMock()
    monkeypatch.setattr(drive_oauth, "refresh_access_token", post)

    assert await drive_oauth.get_valid_drive_access_token("conn1", sm) == "fresh-at"  # type: ignore[arg-type]
    post.assert_not_awaited()
    update.assert_not_awaited()
    assert events == [
        "session:enter",
        "db:get",
        "session:exit",
        "session:enter",
        "db:lock",
        "db:get",
        "session:exit",
    ]


async def test_lock_timeout_raises_a_clear_refresh_error_without_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    import src.integrations.google_drive.oauth as drive_oauth
    from src.db.locks import LockTimeoutError
    from src.security.encryption import encrypt

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, update, _events = _wire_repo(monkeypatch, connection, lock_error=LockTimeoutError("t"))
    post = AsyncMock()
    monkeypatch.setattr(drive_oauth, "refresh_access_token", post)

    with pytest.raises(drive_oauth.TokenRefreshError, match="another refresh"):
        await drive_oauth.get_valid_drive_access_token("conn1", sm)  # type: ignore[arg-type]
    post.assert_not_awaited()
    update.assert_not_awaited()


async def test_lock_key_is_the_connection_row(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    _set_oauth_settings(monkeypatch)
    import src.integrations.google_drive.oauth as drive_oauth
    from src.security.encryption import encrypt

    connection = _connection(
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    sm, _update, _events = _wire_repo(
        monkeypatch, connection, _connection(encrypted_access_token=encrypt("new-at"))
    )
    keys: list[str] = []

    async def _lock(_session: Any, key: str, **_kw: Any) -> None:
        keys.append(key)

    monkeypatch.setattr(locks, "acquire_xact_lock", _lock)
    monkeypatch.setattr(
        drive_oauth,
        "refresh_access_token",
        AsyncMock(return_value={"access_token": "new-at", "expires_in": 3600}),
    )
    await drive_oauth.get_valid_drive_access_token("conn1", sm)  # type: ignore[arg-type]
    assert keys == ["cloud_storage_connection:conn1"]
