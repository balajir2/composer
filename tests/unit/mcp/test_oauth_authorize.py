"""Tests for build_authorize_url."""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from src.mcp.oauth import build_authorize_url
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session
from tests.unit.mcp._oauth_fakes import RepoCalls, script_repo
from tests.unit.mcp._server_fakes import make_server


def _server(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "srv1",
        "user_id": "owner",
        "name": "Highspot",
        "url": "https://api.highspot.com/mcp",
        "auth_type": "oauth",
        "oauth_config": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "scopes": ["read", "write"],
        },
    }
    base.update(overrides)
    return make_server(**base)


def _setup(monkeypatch: pytest.MonkeyPatch) -> tuple[RepoCalls, Any]:
    calls = RepoCalls()
    script_repo(monkeypatch, calls, reap_expired_states=0, create_state=None)
    return calls, RecordingSessionmaker(calls.events)


async def test_authorize_url_has_all_required_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _, sm = _setup(monkeypatch)
    server = _server()
    url = await build_authorize_url(
        server, user_id="user1", redirect_uri="http://localhost:3000/cb", sessionmaker=sm
    )
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "api.highspot.com"
    assert parts.path == "/oauth/authorize"
    params = parse_qs(parts.query)
    # All seven required params present
    assert params["client_id"] == ["client-abc"]
    assert params["redirect_uri"] == ["http://localhost:3000/cb"]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"]
    assert params["state"]
    assert params["scope"] == ["read write"]
    # RFC 8707 resource param — THIS IS FIX #1
    assert params["resource"] == ["https://api.highspot.com"]


async def test_authorize_url_inserts_state_row(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, sm = _setup(monkeypatch)
    server = _server()
    _ = await build_authorize_url(
        server, user_id="user1", redirect_uri="http://localhost:3000/cb", sessionmaker=sm
    )
    ((values,),) = calls.of("create_state")
    assert values["mcp_server_id"] == "srv1"
    assert values["user_id"] == "user1"
    assert values["redirect_uri"] == "http://localhost:3000/cb"
    assert values["code_verifier"]  # PKCE verifier stored
    assert values["state"]
    assert values["scope"] == "read write"
    # expires_at is 5 minutes in the future
    delta = values["expires_at"] - datetime.now(UTC)
    assert 270 <= delta.total_seconds() <= 310


async def test_authorize_url_requires_oauth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, sm = _setup(monkeypatch)
    server = _server(oauth_config=None)
    with pytest.raises(ValueError, match="oauthConfig"):
        await build_authorize_url(
            server, user_id="u", redirect_uri="http://localhost/cb", sessionmaker=sm
        )
    assert calls.calls == []  # nothing touched the database


async def test_authorize_url_runs_state_reaper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expired state rows should be cleaned up on any authorize call."""
    calls, sm = _setup(monkeypatch)
    server = _server()
    _ = await build_authorize_url(
        server, user_id="u", redirect_uri="http://localhost/cb", sessionmaker=sm
    )
    ((now,),) = calls.of("reap_expired_states")
    assert abs((now - datetime.now(UTC)).total_seconds()) < 5


async def test_authorize_reaps_and_creates_in_one_short_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, sm = _setup(monkeypatch)
    await build_authorize_url(
        _server(), user_id="u", redirect_uri="http://localhost/cb", sessionmaker=sm
    )
    assert calls.events == [
        "session:enter",
        "db:reap_expired_states",
        "db:create_state",
        "session:exit",
    ]
    assert_no_network_inside_session(calls.events)
