"""Tests for get_valid_access_token — refresh-on-use + service-account fallback (fix #5)."""

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.db.repos.mcp_oauth import McpOAuthTokenRecord
from src.mcp.oauth import (
    McpTokenExpiredError,
    McpTokenMissingError,
    get_valid_access_token,
)
from src.security.encryption import decrypt, encrypt
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session
from tests.unit.mcp._oauth_fakes import RepoCalls, make_token, script_repo
from tests.unit.mcp._server_fakes import make_server


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server(is_shared: bool = False, owner: str = "owner") -> Any:
    return make_server(
        id="srv1",
        user_id=owner,
        url="https://api.highspot.com/mcp",
        auth_type="oauth",
        is_shared=is_shared,
        oauth_config={
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "c",
            "clientSecret": "s",
        },
    )


def _valid_token(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> McpOAuthTokenRecord:
    _set_enc_key(monkeypatch)
    base: dict[str, Any] = {
        "id": "tok1",
        "user_id": "user1",
        "encrypted_access_token": encrypt("plain-access"),
        "encrypted_refresh_token": encrypt("rt-1"),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    base.update(overrides)
    return make_token(**base)


def _setup(
    monkeypatch: pytest.MonkeyPatch, primary: Any | None, fallback: Any | None = None
) -> tuple[RepoCalls, Any]:
    """get_token is called at most twice (the caller's row, then the owner's)."""
    calls = RepoCalls()
    script_repo(monkeypatch, calls, get_token=[primary, fallback])
    return calls, RecordingSessionmaker(calls.events)


async def test_owner_with_valid_token_returns_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tok = _valid_token(monkeypatch)
    _, sm = _setup(monkeypatch, tok)
    out = await get_valid_access_token(_server(owner="user1"), "user1", sm)
    assert out == "plain-access"


async def test_missing_token_and_not_shared_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    _, sm = _setup(monkeypatch, None)  # user has no token, server not shared
    with pytest.raises(McpTokenMissingError, match="user1"):
        await get_valid_access_token(_server(is_shared=False, owner="owner"), "user1", sm)


async def test_service_account_fallback_for_shared_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIX #5: when user has no token for a shared server, fall back to owner's token."""
    fallback = _valid_token(monkeypatch, user_id="owner")
    calls, sm = _setup(monkeypatch, primary=None, fallback=fallback)
    out = await get_valid_access_token(_server(is_shared=True, owner="owner"), "user1", sm)
    assert out == "plain-access"
    # Two token reads: one for user1, one for owner (both in the same short session).
    assert [args[1:] for args in calls.of("get_token")] == [("user1",), ("owner",)]
    assert calls.events.count("session:enter") == 1


async def test_shared_server_but_owner_also_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    _, sm = _setup(monkeypatch, primary=None, fallback=None)
    with pytest.raises(McpTokenMissingError, match="server owner"):
        await get_valid_access_token(_server(is_shared=True, owner="owner"), "user1", sm)


async def test_near_expiry_triggers_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within expiry buffer, refresh_token is invoked."""
    tok = _valid_token(monkeypatch, expires_at=datetime.now(UTC) + timedelta(seconds=30))
    _, sm = _setup(monkeypatch, tok)

    refresh_calls: list[Any] = []

    async def _fake_refresh(server: Any, token_row: Any, _sm: Any) -> Any:
        refresh_calls.append((server.id, token_row.id))
        # Return a new row with a fresh token
        return make_token(
            encrypted_access_token=encrypt("refreshed"),
            encrypted_refresh_token=token_row.encrypted_refresh_token,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    import src.mcp.oauth as oauth_mod

    monkeypatch.setattr(oauth_mod, "refresh_token", _fake_refresh)

    out = await get_valid_access_token(_server(owner="user1"), "user1", sm)
    assert out == "refreshed"
    assert refresh_calls == [("srv1", "tok1")]


async def test_near_expiry_without_refresh_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tok = _valid_token(
        monkeypatch,
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        encrypted_refresh_token=None,
    )
    _, sm = _setup(monkeypatch, tok)
    with pytest.raises(McpTokenExpiredError):
        await get_valid_access_token(_server(owner="user1"), "user1", sm)


async def test_refresh_reads_close_before_the_post_and_write_the_owners_row(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """Shared-server fallback + expired owner token: short read session, closed; the IdP POST
    runs with no session open; the update goes to the OWNER's row by id (no row for the caller)."""
    owner_token = _valid_token(
        monkeypatch,
        id="owner-tok",
        user_id="owner",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    calls = RepoCalls()
    script_repo(
        monkeypatch,
        calls,
        get_token=[None, owner_token],
        update_token=make_token(
            id="owner-tok",
            encrypted_access_token=encrypt("refreshed"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    sm: Any = RecordingSessionmaker(calls.events)

    def _idp(request: httpx.Request) -> httpx.Response:
        calls.events.append("net:token-post")
        return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})

    httpx_mock.add_callback(_idp, url="https://api.highspot.com/oauth/token", method="POST")

    out = await get_valid_access_token(_server(is_shared=True, owner="owner"), "user1", sm)

    assert out == "refreshed"
    assert calls.events == [
        "session:enter",
        "db:get_token",
        "db:get_token",
        "session:exit",
        "net:token-post",
        "session:enter",
        "db:update_token",
        "session:exit",
    ]
    assert_no_network_inside_session(calls.events)
    ((token_id, _changes),) = calls.of("update_token")
    assert token_id == "owner-tok"
    assert calls.of("upsert_token") == []
    assert decrypt(owner_token.encrypted_access_token) == "plain-access"  # sanity: input untouched
