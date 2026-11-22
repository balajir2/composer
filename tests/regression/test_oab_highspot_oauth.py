"""Regression — OAB highspot-oauth-lifecycle.spec.ts.

OAB source: D:/GitHub/open-agent-builder/tests/highspot-oauth-lifecycle.spec.ts
  Asserts the token-exchange request body includes `resource` (OAB lost
  a multi-day debugging session to this missing param).

This port validates the same invariant in Composer's oauth.py. It does
NOT require a real Highspot instance — it mocks the token endpoint, scripts
the OAuth repo functions, and asserts the POST body.
"""

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.mcp.oauth import exchange_code_for_tokens, refresh_token
from src.security.encryption import encrypt
from tests.unit.db_fakes import RecordingSessionmaker
from tests.unit.mcp._oauth_fakes import (
    RepoCalls,
    make_state,
    make_token,
    script_lock,
    script_repo,
)
from tests.unit.mcp._server_fakes import make_server

pytestmark = pytest.mark.integration


def _set_enc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _highspot_server() -> Any:
    return make_server(
        id="srv-hs",
        user_id="dev",
        url="https://mcp.highspot.com/mcp",
        auth_type="oauth",
        oauth_config={
            "authorizeUrl": "https://mcp.highspot.com/oauth/authorize",
            "tokenUrl": "https://mcp.highspot.com/oauth/token",
            "clientId": "hs-client",
            "clientSecret": "hs-secret",
            "scopes": ["read"],
        },
    )


async def test_oab_regression_token_exchange_includes_resource(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """OAB-caught: token exchange MUST carry resource=https://mcp.highspot.com.

    Without this, Highspot responds with 'resource mismatch' and auth fails.
    """
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://mcp.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
    )

    calls = RepoCalls()
    script_repo(
        monkeypatch,
        calls,
        consume_state=make_state(mcp_server_id="srv-hs", state="s1", code_verifier="cv"),
        upsert_token=make_token(),
    )
    sm: Any = RecordingSessionmaker(calls.events)

    await exchange_code_for_tokens(_highspot_server(), code="c", state="s1", sessionmaker=sm)

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fmcp.highspot.com" in body, (
        f"Regression: token exchange must include resource param. Body was: {body}"
    )


async def test_oab_regression_refresh_includes_resource(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    """OAB-caught: refresh MUST carry resource too — Highspot enforces it on refresh."""
    _set_enc_key(monkeypatch)
    httpx_mock.add_response(  # pyright: ignore[reportUnknownMemberType]
        url="https://mcp.highspot.com/oauth/token",
        method="POST",
        json={"access_token": "new-at", "expires_in": 3600},
    )

    token_row = make_token(
        mcp_server_id="srv-hs",
        user_id="dev",
        encrypted_access_token=encrypt("old-at"),
        encrypted_refresh_token=encrypt("rt-1"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        scope="read",
    )
    calls = RepoCalls()
    script_repo(monkeypatch, calls, get_token_by_id=token_row, update_token=make_token())
    script_lock(monkeypatch, calls.events)
    sm: Any = RecordingSessionmaker(calls.events)

    await refresh_token(_highspot_server(), token_row, sm)

    req = httpx_mock.get_request()  # pyright: ignore[reportUnknownMemberType]
    assert req is not None
    body = req.content.decode()
    assert "resource=https%3A%2F%2Fmcp.highspot.com" in body, (
        f"Regression: refresh must include resource. Body was: {body}"
    )
