"""Tests for /mcp-servers/:id/oauth/* and /oauth/callback endpoints."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "dev",
        "name": "Highspot",
        "url": "https://api.highspot.com/mcp",
        "description": None,
        "category": None,
        "authType": "oauth",
        "encryptedAccessToken": None,
        "headerName": None,
        "oauthConfig": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "c",
            "clientSecret": "s",
            "scopes": ["read"],
        },
        "tools": None,
        "connectionStatus": "untested",
        "lastTested": None,
        "lastError": None,
        "enabled": True,
        "isOfficial": False,
        "isShared": False,
        "headers": None,
        "createdAt": "2026-04-20T00:00:00Z",
        "updatedAt": "2026-04-20T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.mcpserver = MagicMock()
    db.mcpserver.find_unique = AsyncMock(return_value=_server_row())
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.find_unique = AsyncMock(return_value=None)
    db.mcpoauthtoken.delete = AsyncMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.create = AsyncMock()
    db.mcpoauthstate.delete_many = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_authorize_returns_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()
    resp = client.post(
        "/mcp-servers/srv1/oauth/authorize",
        json={"redirectUri": "http://localhost:3000/oauth/callback"},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorizeUrl"]
    assert "api.highspot.com/oauth/authorize" in url
    assert "resource=https%3A%2F%2Fapi.highspot.com" in url
    db.mcpoauthstate.create.assert_awaited_once()


def test_authorize_on_non_oauth_server_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.mcpserver.find_unique = AsyncMock(return_value=_server_row(authType="none"))
    resp = client.post(
        "/mcp-servers/srv1/oauth/authorize",
        json={"redirectUri": "http://localhost/cb"},
    )
    assert resp.status_code == 422
    assert "authType" in resp.json()["detail"]


def test_authorize_server_not_found_404() -> None:
    client, db = _client_with_mock_db()
    db.mcpserver.find_unique = AsyncMock(return_value=None)
    resp = client.post(
        "/mcp-servers/ghost/oauth/authorize",
        json={"redirectUri": "http://localhost/cb"},
    )
    assert resp.status_code == 404


def test_callback_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()

    state_row = SimpleNamespace(
        id="st1",
        mcpServerId="srv1",
        userId="dev",
        state="abc",
        codeVerifier="v",
        redirectUri="http://localhost/cb",
        scope="read",
        expiresAt=datetime.now(UTC) + timedelta(minutes=4),
    )
    db.mcpoauthstate.find_unique = AsyncMock(return_value=state_row)
    db.mcpoauthstate.delete = AsyncMock()
    db.mcpoauthtoken.upsert = AsyncMock(return_value=SimpleNamespace(id="tok1"))

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, db: Any) -> Any:
        return SimpleNamespace(id="tok1", mcpServerId="srv1", userId="dev")

    monkeypatch.setattr(api_mod, "exchange_code_for_tokens", _fake_exchange)

    # Callback now redirects the browser back to the admin MCP page with a
    # status param so the UI can toast instead of dumping raw JSON.
    resp = client.get("/oauth/callback?code=c1&state=abc", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "/admin/mcp-servers" in location
    assert "oauth=success" in location
    assert "serverId=srv1" in location


def test_callback_unknown_state_redirects_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.mcpoauthstate.find_unique = AsyncMock(return_value=None)

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, db: Any) -> Any:
        from src.mcp.oauth import InvalidStateError

        raise InvalidStateError("unknown state")

    monkeypatch.setattr(api_mod, "exchange_code_for_tokens", _fake_exchange)

    resp = client.get("/oauth/callback?code=c&state=missing", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "oauth=error" in location
    assert "detail=" in location


def test_disconnect_owner_allowed() -> None:
    client, db = _client_with_mock_db()
    db.mcpoauthtoken.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="tok1", mcpServerId="srv1", userId="dev")
    )
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    db.mcpoauthtoken.delete.assert_awaited_once()


def test_disconnect_idempotent_when_no_token() -> None:
    client, db = _client_with_mock_db()
    db.mcpoauthtoken.find_unique = AsyncMock(return_value=None)
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    db.mcpoauthtoken.delete.assert_not_awaited()
