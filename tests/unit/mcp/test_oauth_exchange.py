"""Tests for exchange_code_for_tokens — including the resource-param assertion."""

import base64
import os
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.mcp.oauth import InvalidStateError, TokenExchangeError, exchange_code_for_tokens
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session
from tests.unit.mcp._oauth_fakes import RepoCalls, make_state, make_token, script_repo
from tests.unit.mcp._server_fakes import make_server


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": "srv1",
        "user_id": "owner",
        "url": "https://api.highspot.com/mcp",
        "auth_type": "oauth",
        "oauth_config": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": "plain-secret-for-test",
            "scopes": ["read"],
        },
    }
    base.update(overrides)
    return make_server(**base)


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    consume: Any,
    existing_state: Any = None,
) -> tuple[RepoCalls, Any]:
    """Script consume_state (and the follow-up get_state) and the token upsert."""
    _set_enc_key(monkeypatch)
    calls = RepoCalls()
    script_repo(
        monkeypatch,
        calls,
        consume_state=consume,
        get_state=existing_state,
        upsert_token=make_token(),
    )
    return calls, RecordingSessionmaker(calls.events)


async def test_exchange_sends_resource_param(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """FIX #1 (second of four): token-exchange POST must include resource param."""
    _, sm = _setup(monkeypatch, consume=make_state())
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
    )
    await exchange_code_for_tokens(_server(), code="code-1", state="abc", sessionmaker=sm)

    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    # Content-Type is form-urlencoded
    assert "resource=https%3A%2F%2Fapi.highspot.com" in body
    assert "grant_type=authorization_code" in body
    assert "code=code-1" in body
    assert "code_verifier=ver123" in body


async def test_exchange_stores_encrypted_tokens(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    calls, sm = _setup(monkeypatch, consume=make_state())
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "plain-at", "refresh_token": "plain-rt", "expires_in": 3600},
    )
    await exchange_code_for_tokens(_server(), code="c", state="abc", sessionmaker=sm)
    ((server_id, user_id, values),) = calls.of("upsert_token")
    assert server_id == "srv1"
    assert user_id == "user1"
    assert values["encrypted_access_token"] != "plain-at"
    assert values["encrypted_access_token"]
    assert values["encrypted_refresh_token"] != "plain-rt"
    assert values["scope"] == "read"  # falls back to the scope recorded with the state
    assert values["token_type"] == "Bearer"


async def test_exchange_consumes_state_once(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    calls, sm = _setup(monkeypatch, consume=make_state())
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "expires_in": 3600},
    )
    await exchange_code_for_tokens(_server(), code="c", state="abc", sessionmaker=sm)
    assert [args[0] for args in calls.of("consume_state")] == ["abc"]


async def test_exchange_decrypts_encrypted_client_secret(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """P0-5: oauthConfig.clientSecret is now encrypted at rest by the
    mcp-servers API — the exchange must decrypt it before sending, or the
    IdP's token endpoint receives ciphertext as client_secret."""
    from src.security.encryption import encrypt_marked

    _, sm = _setup(monkeypatch, consume=make_state())
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "expires_in": 3600},
    )
    server = _server(
        oauth_config={
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": encrypt_marked("real-client-secret"),
            "scopes": ["read"],
        }
    )
    await exchange_code_for_tokens(server, code="c", state="abc", sessionmaker=sm)

    req = httpx_mock.get_request()
    assert req is not None
    body = req.content.decode()
    assert "client_secret=real-client-secret" in body


async def test_exchange_raises_for_unknown_or_replayed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, sm = _setup(monkeypatch, consume=None, existing_state=None)  # nothing consumed, no row
    with pytest.raises(InvalidStateError, match="not found"):
        await exchange_code_for_tokens(_server(), code="c", state="missing", sessionmaker=sm)
    assert calls.of("upsert_token") == []


async def test_exchange_raises_for_expired_state_and_leaves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # consume_state returns nothing for an expired row, but the row is still there.
    calls, sm = _setup(monkeypatch, consume=None, existing_state=make_state())
    with pytest.raises(InvalidStateError, match="expired"):
        await exchange_code_for_tokens(_server(), code="c", state="abc", sessionmaker=sm)
    # Nothing stored, and the IdP was never called (no httpx response is registered).
    assert calls.of("upsert_token") == []


async def test_exchange_raises_on_idp_error_after_consuming_state(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    calls, sm = _setup(monkeypatch, consume=make_state())
    httpx_mock.add_response(
        url="https://api.highspot.com/oauth/token",
        method="POST",
        status_code=400,
        json={"error": "invalid_grant"},
    )
    with pytest.raises(TokenExchangeError, match="400"):
        await exchange_code_for_tokens(_server(), code="c", state="abc", sessionmaker=sm)
    assert len(calls.of("consume_state")) == 1  # consumed before the IdP call
    assert calls.of("upsert_token") == []  # nothing stored


async def test_exchange_orders_consume_then_post_then_store_with_no_session_on_the_post(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    calls, sm = _setup(monkeypatch, consume=make_state())

    def _idp(request: httpx.Request) -> httpx.Response:
        calls.events.append("net:token-post")
        return httpx.Response(200, json={"access_token": "at", "expires_in": 60})

    httpx_mock.add_callback(_idp, url="https://api.highspot.com/oauth/token", method="POST")
    await exchange_code_for_tokens(_server(), code="c", state="abc", sessionmaker=sm)
    assert calls.events == [
        "session:enter",
        "db:consume_state",
        "session:exit",
        "net:token-post",
        "session:enter",
        "db:upsert_token",
        "session:exit",
    ]
    assert_no_network_inside_session(calls.events)
