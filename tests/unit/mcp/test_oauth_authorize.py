"""Tests for build_authorize_url."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

from src.mcp.oauth import build_authorize_url


def _server(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "owner",
        "name": "Highspot",
        "url": "https://api.highspot.com/mcp",
        "oauthConfig": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "scopes": ["read", "write"],
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.create = AsyncMock()
    db.mcpoauthstate.delete_many = AsyncMock()
    return db


async def test_authorize_url_has_all_required_params() -> None:
    db = _mock_db()
    server = _server()
    url = await build_authorize_url(
        server, user_id="user1", redirect_uri="http://localhost:3000/cb", db=db
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


async def test_authorize_url_inserts_state_row() -> None:
    db = _mock_db()
    server = _server()
    _ = await build_authorize_url(
        server, user_id="user1", redirect_uri="http://localhost:3000/cb", db=db
    )
    db.mcpoauthstate.create.assert_awaited_once()
    create_args = db.mcpoauthstate.create.await_args
    data = create_args.kwargs["data"]
    assert data["mcpServerId"] == "srv1"
    assert data["userId"] == "user1"
    assert data["redirectUri"] == "http://localhost:3000/cb"
    assert data["codeVerifier"]  # PKCE verifier stored
    assert data["state"]
    # expires_at is 5 minutes in the future
    delta = data["expiresAt"] - datetime.now(UTC)
    assert 270 <= delta.total_seconds() <= 310


async def test_authorize_url_requires_oauth_config() -> None:
    db = _mock_db()
    server = _server(oauthConfig=None)
    with pytest.raises(ValueError, match="oauthConfig"):
        await build_authorize_url(server, user_id="u", redirect_uri="http://localhost/cb", db=db)


async def test_authorize_url_runs_state_reaper() -> None:
    """Expired state rows should be cleaned up on any authorize call."""
    db = _mock_db()
    server = _server()
    _ = await build_authorize_url(server, user_id="u", redirect_uri="http://localhost/cb", db=db)
    db.mcpoauthstate.delete_many.assert_awaited_once()
    call = db.mcpoauthstate.delete_many.await_args
    # Filter is expiresAt lt now
    assert "where" in call.kwargs
