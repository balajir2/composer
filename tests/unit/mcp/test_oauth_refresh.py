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
    assert keys == ["mcp_oauth_token:srv1:user1"]  # the natural key: server and user
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


async def test_refused_waiter_holds_no_session_while_it_waits_then_returns_the_fresh_token(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Refused the lock: roll back and close the session, sleep, re-read in a SHORT session;
    the winner's token is fresh, so return it. No session is open while it waits."""
    tok = _token_row(monkeypatch)
    fresh = make_token(
        encrypted_access_token=encrypt("fresh-at"),
        encrypted_refresh_token=encrypt("refresh-456"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    calls, sm = _setup()
    _script(monkeypatch, calls, fresh)
    script_lock(monkeypatch, calls.events, grants=[False])
    out = await refresh_token(_server(), tok, sm)
    assert out == fresh
    assert httpx_mock.get_requests() == []
    assert calls.events == [
        "session:enter",
        "db:lock-denied",
        "session:exit",
        "session:enter",
        "db:get_token_by_id",
        "session:exit",
    ]
    assert_no_network_inside_session(calls.events)  # no exception needed: nothing ran inside
    sm.session.rollback.assert_awaited()


async def test_refused_waiter_retries_the_lock_while_the_token_is_still_stale(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)  # always still expired
    script_lock(monkeypatch, calls.events, grants=[False, False, True])
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 60},
    )
    await refresh_token(_server(), tok, sm)
    assert calls.events.count("db:lock-denied") == 2
    assert len(httpx_mock.get_requests()) == 1


async def test_waiter_deadline_raises_a_clear_refresh_error_without_posting(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    import src.mcp.oauth as oauth_mod

    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)
    script_lock(monkeypatch, calls.events, grants=[False])
    monkeypatch.setattr(oauth_mod, "_REFRESH_WAIT_DEADLINE_S", 0.05)
    with pytest.raises(TokenRefreshError, match="Timed out waiting for a concurrent token refresh"):
        await refresh_token(_server(), tok, sm)
    assert httpx_mock.get_requests() == []
    assert calls.of("update_token") == []


async def test_refresh_post_is_bounded_by_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import src.mcp.oauth as oauth_mod

    tok = _token_row(monkeypatch)
    calls, sm = _setup()
    _script(monkeypatch, calls, tok)

    async def _hang(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30)

    monkeypatch.setattr(oauth_mod, "_post_token_form", _hang)
    monkeypatch.setattr(oauth_mod, "_REFRESH_POST_TIMEOUT_S", 0.05)
    with pytest.raises(TokenRefreshError, match="timed out"):
        await refresh_token(_server(), tok, sm)
    assert calls.of("update_token") == []
    assert _REFRESH_DEADLINE_EXCEEDS_POST_TIMEOUT()


def _REFRESH_DEADLINE_EXCEEDS_POST_TIMEOUT() -> bool:
    import src.mcp.oauth as oauth_mod

    return oauth_mod._REFRESH_WAIT_DEADLINE_S > oauth_mod._REFRESH_POST_TIMEOUT_S  # pyright: ignore[reportPrivateUsage]
