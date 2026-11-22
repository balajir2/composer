"""Tests for refresh_token — third resource-param assertion."""

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.mcp.oauth import McpTokenMissingError, TokenRefreshError, refresh_token
from src.security.encryption import encrypt
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session
from tests.unit.mcp._oauth_fakes import RepoCalls, make_token, script_lock, script_repo
from tests.unit.mcp._server_fakes import make_server


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server() -> Any:
    return make_server(
        id="srv1",
        user_id="owner",
        url="https://api.highspot.com/mcp",
        auth_type="oauth",
        oauth_config={
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": "plain-secret",
        },
    )


def _token_row(monkeypatch: pytest.MonkeyPatch) -> Any:
    _set_enc_key(monkeypatch)
    return make_token(
        encrypted_access_token=encrypt("old-access"),
        encrypted_refresh_token=encrypt("refresh-123"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        scope="read",
    )


def _setup() -> tuple[RepoCalls, Any]:
    calls = RepoCalls()
    return calls, RecordingSessionmaker(calls.events)


def _script(
    monkeypatch: pytest.MonkeyPatch, calls: RepoCalls, current: Any, result: Any = ...
) -> list[str]:
    """Script the lock, the under-lock re-read (``current``) and the update; returns lock keys."""
    script_repo(
        monkeypatch,
        calls,
        get_token_by_id=current,
        update_token=make_token() if result is ... else result,
    )
    return script_lock(monkeypatch, calls.events)


async def test_refresh_sends_resource_param(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """FIX #1 (third of four): refresh POST must include resource param."""
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )
    await refresh_token(_server(), tok, sm)
    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fapi.highspot.com" in body
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh-123" in body


async def test_refresh_updates_token_row_by_id(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
    )
    await refresh_token(_server(), tok, sm)
    ((token_id, changes),) = calls.of("update_token")
    assert token_id == "tok1"
    assert changes["encrypted_access_token"] != "new-at"  # encrypted
    assert changes["encrypted_refresh_token"] != "new-rt"
    assert changes["expires_at"] > datetime.now(UTC) + timedelta(minutes=50)
    # Only the columns a refresh changes: scope, token type and created_at are never written.
    assert set(changes) == {"encrypted_access_token", "encrypted_refresh_token", "expires_at"}


async def test_refresh_keeps_old_refresh_token_if_idp_omits(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Some IdPs don't rotate the refresh token on refresh. Keep the existing one."""
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},  # no refresh_token
    )
    await refresh_token(_server(), tok, sm)
    ((_, changes),) = calls.of("update_token")
    # The column is not in the update at all, so the stored refresh token is untouched.
    assert "encrypted_refresh_token" not in changes


async def test_refresh_raises_on_idp_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        status_code=401,
        json={"error": "invalid_grant"},
    )
    with pytest.raises(TokenRefreshError, match="401"):
        await refresh_token(_server(), tok, sm)
    assert calls.of("update_token") == []


async def test_refresh_of_a_token_removed_meanwhile_raises(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok, result=None)  # update_token finds no row
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )
    with pytest.raises(McpTokenMissingError):
        await refresh_token(_server(), tok, sm)


async def test_refresh_holds_the_lock_for_the_post_and_orders_lock_reread_post_write(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Documented exception to the short-session rule: the refresh POST is the ONLY network
    call inside an open session, and it runs while the advisory lock is held."""
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    keys = _script(monkeypatch, calls, tok)

    def _idp(request: httpx.Request) -> httpx.Response:
        calls.events.append("net:token-post")
        return httpx.Response(200, json={"access_token": "new-at", "expires_in": 60})

    httpx_mock.add_callback(_idp, url="https://api.highspot.com/oauth/token", method="POST")
    await refresh_token(_server(), tok, sm)
    assert calls.events == [
        "session:enter",
        "db:lock",
        "db:get_token_by_id",
        "net:token-post",
        "db:update_token",
        "session:exit",
    ]
    assert keys == ["mcp_oauth_token:tok1"]
    assert_no_network_inside_session(calls.events, allowed_inside=frozenset({"net:token-post"}))


async def test_refresh_skips_the_post_when_another_caller_just_refreshed(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """The re-read under the lock shows a fresh token: return it, no POST, no write."""
    tok = _token_row(monkeypatch)
    fresh = make_token(
        encrypted_access_token=encrypt("fresh-at"),
        encrypted_refresh_token=encrypt("refresh-456"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    calls, sm = _setup()
    _script(monkeypatch, calls, fresh)
    out = await refresh_token(_server(), tok, sm)
    assert out == fresh
    assert httpx_mock.get_requests() == []
    assert calls.of("update_token") == []
    assert calls.events == ["session:enter", "db:lock", "db:get_token_by_id", "session:exit"]


async def test_refresh_of_a_token_deleted_before_the_lock_was_granted_raises_without_posting(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, None)
    with pytest.raises(McpTokenMissingError):
        await refresh_token(_server(), tok, sm)
    assert httpx_mock.get_requests() == []


async def test_lock_timeout_raises_a_clear_refresh_error_without_posting(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    from src.db.locks import LockTimeoutError

    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)
    script_lock(monkeypatch, calls.events, error=LockTimeoutError("timed out"))
    with pytest.raises(TokenRefreshError, match="another refresh"):
        await refresh_token(_server(), tok, sm)
    assert httpx_mock.get_requests() == []
    assert calls.of("update_token") == []
