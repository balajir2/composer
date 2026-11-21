"""Tests for /mcp-servers/:id/oauth/* and /oauth/callback endpoints."""

import base64
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import users as users_repo
from src.db.repos.mcp_servers import McpServerRecord
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSessionmaker, override_session


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server_row(**overrides: Any) -> McpServerRecord:
    when = datetime(2026, 4, 20, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "srv1",
        "user_id": "dev",
        "name": "Highspot",
        "url": "https://api.highspot.com/mcp",
        "description": None,
        "category": None,
        "auth_type": "oauth",
        "encrypted_access_token": None,
        "header_name": None,
        "oauth_config": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "c",
            "clientSecret": "s",
            "scopes": ["read"],
        },
        "tools": None,
        "connection_status": "untested",
        "last_tested": None,
        "last_error": None,
        "enabled": True,
        "is_official": False,
        "is_shared": False,
        "headers": None,
        "created_at": when,
        "updated_at": when,
        "original_owner_email": None,
    }
    base.update(overrides)
    return McpServerRecord(**base)


class _ServerRepo:
    """The server repo's functions as AsyncMocks; assigning one re-scripts it for the test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        object.__setattr__(self, "_mp", monkeypatch)
        self.get = AsyncMock(return_value=_server_row())
        self.update = AsyncMock(return_value=_server_row())

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        object.__getattribute__(self, "_mp").setattr(mcp_servers_repo, name, value)


def _client_with_mock_db(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, MagicMock, _ServerRepo]:
    app = create_app()
    server_repo = _ServerRepo(monkeypatch)
    for name in ("get", "update"):
        monkeypatch.setattr(mcp_servers_repo, name, getattr(server_repo, name))
    db = MagicMock()
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.find_unique = AsyncMock(return_value=None)
    db.mcpoauthtoken.delete = AsyncMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.create = AsyncMock()
    db.mcpoauthstate.delete_many = AsyncMock()
    session = override_session(app)
    app.state.db = db
    app.state.db_sessionmaker = FakeSessionmaker(session)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=None))
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db, server_repo


def test_authorize_returns_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db, _ = _client_with_mock_db(monkeypatch)
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
    client, _, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(return_value=_server_row(auth_type="none"))
    resp = client.post(
        "/mcp-servers/srv1/oauth/authorize",
        json={"redirectUri": "http://localhost/cb"},
    )
    assert resp.status_code == 422
    assert "authType" in resp.json()["detail"]


def test_update_oauth_config_encrypts_client_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """P0-5: same encryption treatment as create — the PATCH path must not
    persist a fresh plaintext clientSecret either."""
    _set_enc_key(monkeypatch)
    client, _, repo = _client_with_mock_db(monkeypatch)
    repo.update = AsyncMock(return_value=_server_row())

    resp = client.patch(
        "/mcp-servers/srv1/oauth-config",
        json={
            "oauthConfig": {
                "authorizeUrl": "https://api.highspot.com/oauth/authorize",
                "tokenUrl": "https://api.highspot.com/oauth/token",
                "clientId": "client-abc",
                "clientSecret": "brand-new-plain-secret",
            }
        },
    )
    assert resp.status_code == 200, resp.text

    call_args = repo.update.await_args
    stored_config = call_args.args[2]["oauth_config"]  # type: ignore[union-attr]
    assert stored_config["clientSecret"] != "brand-new-plain-secret"

    from src.security.encryption import decrypt_marked

    assert decrypt_marked(stored_config["clientSecret"]) == "brand-new-plain-secret"


def test_authorize_server_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(return_value=None)
    resp = client.post(
        "/mcp-servers/ghost/oauth/authorize",
        json={"redirectUri": "http://localhost/cb"},
    )
    assert resp.status_code == 404


def test_callback_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, db, _ = _client_with_mock_db(monkeypatch)

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
    client, db, _ = _client_with_mock_db(monkeypatch)
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


def test_disconnect_owner_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db, _ = _client_with_mock_db(monkeypatch)
    db.mcpoauthtoken.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="tok1", mcpServerId="srv1", userId="dev")
    )
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    db.mcpoauthtoken.delete.assert_awaited_once()


def test_disconnect_idempotent_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db, _ = _client_with_mock_db(monkeypatch)
    db.mcpoauthtoken.find_unique = AsyncMock(return_value=None)
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    db.mcpoauthtoken.delete.assert_not_awaited()


# --- visibility on authorize -------------------------------------------------

_AUTHZ_BODY = {"redirectUri": "http://localhost/cb"}


def test_authorize_private_server_of_other_user_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    client, db, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(
        return_value=_server_row(id="other", user_id="someone-else", is_shared=False)
    )
    resp = client.post("/mcp-servers/other/oauth/authorize", json=_AUTHZ_BODY)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "MCP server 'other' not found."}
    db.mcpoauthstate.create.assert_not_awaited()


def test_authorize_private_404_identical_to_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    client, _, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(
        return_value=_server_row(id="same", user_id="someone-else", is_shared=False)
    )
    private = client.post("/mcp-servers/same/oauth/authorize", json=_AUTHZ_BODY)
    repo.get = AsyncMock(return_value=None)
    unknown = client.post("/mcp-servers/same/oauth/authorize", json=_AUTHZ_BODY)
    assert private.status_code == unknown.status_code == 404
    assert private.content == unknown.content


def test_authorize_shared_server_of_other_user_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    client, db, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(return_value=_server_row(user_id="someone-else", is_shared=True))
    resp = client.post("/mcp-servers/srv1/oauth/authorize", json=_AUTHZ_BODY)
    assert resp.status_code == 200, resp.text
    db.mcpoauthstate.create.assert_awaited_once()
