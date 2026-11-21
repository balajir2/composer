"""Tests for /mcp-servers/:id/oauth/* and /oauth/callback endpoints."""

import base64
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import users as users_repo
from src.db.repos.mcp_servers import McpServerRecord
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import (
    FakeSessionmaker,
    RecordingSessionmaker,
    assert_no_network_inside_session,
    override_session,
)
from tests.unit.mcp._oauth_fakes import RepoCalls, make_state, make_token, script_repo


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
        self.get: Any = AsyncMock(return_value=_server_row())
        self.update: Any = AsyncMock(return_value=_server_row())

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        object.__getattribute__(self, "_mp").setattr(mcp_servers_repo, name, value)


def _client_with_mock_db(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, RepoCalls, _ServerRepo]:
    app = create_app()
    server_repo = _ServerRepo(monkeypatch)
    for name in ("get", "update"):
        monkeypatch.setattr(mcp_servers_repo, name, getattr(server_repo, name))
    calls = RepoCalls()
    script_repo(
        monkeypatch,
        calls,
        reap_expired_states=0,
        create_state=None,
        get_state=None,
        get_token=None,
        delete_token=True,
    )
    session = override_session(app)
    app.state.db_sessionmaker = FakeSessionmaker(session)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=None))
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), calls, server_repo


def test_authorize_returns_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_enc_key(monkeypatch)
    client, calls, _ = _client_with_mock_db(monkeypatch)
    resp = client.post(
        "/mcp-servers/srv1/oauth/authorize",
        json={"redirectUri": "http://localhost:3000/oauth/callback"},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorizeUrl"]
    assert "api.highspot.com/oauth/authorize" in url
    assert "resource=https%3A%2F%2Fapi.highspot.com" in url
    assert len(calls.of("create_state")) == 1


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
    client, calls, _ = _client_with_mock_db(monkeypatch)
    script_repo(monkeypatch, calls, get_state=make_state(mcp_server_id="srv1"))

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, sessionmaker: Any) -> Any:
        return make_token()

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
    client, _, _ = _client_with_mock_db(monkeypatch)  # get_state answers None

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, sessionmaker: Any) -> Any:
        raise AssertionError("an unknown state must not reach the token exchange")

    monkeypatch.setattr(api_mod, "exchange_code_for_tokens", _fake_exchange)

    resp = client.get("/oauth/callback?code=c&state=missing", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "oauth=error" in location
    assert "detail=OAuth+state+not+found+%28CSRF+or+replay%29." in location
    assert "serverId=&" in location  # empty serverId: the state was never found


def test_callback_deleted_server_redirects_error_without_consuming_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls, repo = _client_with_mock_db(monkeypatch)
    script_repo(monkeypatch, calls, get_state=make_state(mcp_server_id="gone"))
    repo.get = AsyncMock(return_value=None)

    resp = client.get("/oauth/callback?code=c&state=abc", follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "oauth=error" in location and "serverId=gone" in location
    assert "Server+not+found+for+this+OAuth+state." in location
    assert calls.of("consume_state") == []


def test_callback_invalid_state_from_the_exchange_redirects_with_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls, _ = _client_with_mock_db(monkeypatch)
    script_repo(monkeypatch, calls, get_state=make_state(mcp_server_id="srv1"))

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, sessionmaker: Any) -> Any:
        from src.mcp.oauth import InvalidStateError

        raise InvalidStateError("OAuth state 'abc' has expired.")

    monkeypatch.setattr(api_mod, "exchange_code_for_tokens", _fake_exchange)
    resp = client.get("/oauth/callback?code=c&state=abc", follow_redirects=False)
    location = resp.headers["location"]
    assert "oauth=error" in location and "serverId=srv1" in location
    assert "has+expired" in location


def test_disconnect_owner_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls, _ = _client_with_mock_db(monkeypatch)
    script_repo(monkeypatch, calls, get_token=make_token(id="tok1"), delete_token=True)
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    assert calls.of("get_token") == [("srv1", "dev")]
    assert calls.of("delete_token") == [("tok1",)]


def test_disconnect_idempotent_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls, _ = _client_with_mock_db(monkeypatch)
    resp = client.post("/mcp-servers/srv1/oauth/disconnect")
    assert resp.status_code == 204
    assert calls.of("delete_token") == []


# --- visibility on authorize -------------------------------------------------

_AUTHZ_BODY = {"redirectUri": "http://localhost/cb"}


def test_authorize_private_server_of_other_user_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    client, calls, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(
        return_value=_server_row(id="other", user_id="someone-else", is_shared=False)
    )
    resp = client.post("/mcp-servers/other/oauth/authorize", json=_AUTHZ_BODY)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "MCP server 'other' not found."}
    assert calls.of("create_state") == []


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
    client, calls, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(return_value=_server_row(user_id="someone-else", is_shared=True))
    resp = client.post("/mcp-servers/srv1/oauth/authorize", json=_AUTHZ_BODY)
    assert resp.status_code == 200, resp.text
    assert len(calls.of("create_state")) == 1


def test_callback_holds_no_session_across_the_token_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enc_key(monkeypatch)
    client, calls, repo = _client_with_mock_db(monkeypatch)
    events = calls.events
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]
    script_repo(monkeypatch, calls, get_state=make_state(mcp_server_id="srv1"))

    async def _get(*_a: object, **_k: object) -> McpServerRecord:
        events.append("db:get")
        return _server_row()

    repo.get = _get

    import src.api.mcp_servers as api_mod

    async def _fake_exchange(server: Any, code: str, state: str, sessionmaker: Any) -> Any:
        events.append("net:exchange")

    monkeypatch.setattr(api_mod, "exchange_code_for_tokens", _fake_exchange)

    resp = client.get("/oauth/callback?code=c1&state=abc", follow_redirects=False)
    assert resp.status_code == 303
    assert "oauth=success" in resp.headers["location"]
    assert events == [
        "session:enter",
        "db:get_state",
        "db:get",
        "session:exit",
        "net:exchange",
    ]
    assert_no_network_inside_session(events)
