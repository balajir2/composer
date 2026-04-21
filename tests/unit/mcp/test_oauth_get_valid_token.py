"""Tests for get_valid_access_token — refresh-on-use + service-account fallback (fix #5)."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mcp.oauth import (
    McpTokenExpiredError,
    McpTokenMissingError,
    get_valid_access_token,
)
from src.security.encryption import encrypt


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server(is_shared: bool = False, owner: str = "owner") -> Any:
    return SimpleNamespace(
        id="srv1",
        userId=owner,
        url="https://api.highspot.com/mcp",
        isShared=is_shared,
        oauthConfig={
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "c",
            "clientSecret": "s",
        },
    )


def _valid_token(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Any:
    _set_enc_key(monkeypatch)
    base: dict[str, Any] = {
        "id": "tok1",
        "mcpServerId": "srv1",
        "userId": "user1",
        "encryptedAccessToken": encrypt("plain-access"),
        "encryptedRefreshToken": encrypt("rt-1"),
        "expiresAt": datetime.now(UTC) + timedelta(hours=1),
        "tokenType": "Bearer",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_db(primary: Any | None, fallback: Any | None = None) -> MagicMock:
    """mcpoauthtoken.find_unique is called at most twice (primary, then fallback)."""
    db = MagicMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.find_unique = AsyncMock(side_effect=[primary, fallback])
    db.mcpoauthtoken.update = AsyncMock()
    return db


async def test_owner_with_valid_token_returns_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tok = _valid_token(monkeypatch)
    db = _mock_db(tok)
    out = await get_valid_access_token(_server(owner="user1"), "user1", db)
    assert out == "plain-access"


async def test_missing_token_and_not_shared_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    db = _mock_db(None)  # user has no token, server not shared
    with pytest.raises(McpTokenMissingError, match="user1"):
        await get_valid_access_token(_server(is_shared=False, owner="owner"), "user1", db)


async def test_service_account_fallback_for_shared_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIX #5: when user has no token for a shared server, fall back to owner's token."""
    fallback = _valid_token(monkeypatch, userId="owner")
    db = _mock_db(primary=None, fallback=fallback)
    out = await get_valid_access_token(_server(is_shared=True, owner="owner"), "user1", db)
    assert out == "plain-access"
    # Two find_unique calls: one for user1, one for owner
    assert db.mcpoauthtoken.find_unique.await_count == 2


async def test_shared_server_but_owner_also_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    db = _mock_db(primary=None, fallback=None)
    with pytest.raises(McpTokenMissingError, match="server owner"):
        await get_valid_access_token(_server(is_shared=True, owner="owner"), "user1", db)


async def test_near_expiry_triggers_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within expiry buffer, refresh_token is invoked."""
    tok = _valid_token(monkeypatch, expiresAt=datetime.now(UTC) + timedelta(seconds=30))
    db = _mock_db(tok)

    refresh_calls: list[Any] = []

    async def _fake_refresh(server: Any, token_row: Any, _db: Any) -> Any:
        refresh_calls.append((server.id, token_row.id))
        # Return a new row with a fresh token
        return SimpleNamespace(
            id="tok1",
            encryptedAccessToken=encrypt("refreshed"),
            encryptedRefreshToken=token_row.encryptedRefreshToken,
            expiresAt=datetime.now(UTC) + timedelta(hours=1),
        )

    import src.mcp.oauth as oauth_mod

    monkeypatch.setattr(oauth_mod, "refresh_token", _fake_refresh)

    out = await get_valid_access_token(_server(owner="user1"), "user1", db)
    assert out == "refreshed"
    assert refresh_calls == [("srv1", "tok1")]


async def test_near_expiry_without_refresh_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tok = _valid_token(
        monkeypatch,
        expiresAt=datetime.now(UTC) + timedelta(seconds=30),
        encryptedRefreshToken=None,
    )
    db = _mock_db(tok)
    with pytest.raises(McpTokenExpiredError):
        await get_valid_access_token(_server(owner="user1"), "user1", db)
