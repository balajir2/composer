"""Tests for the Google Drive OAuth connect routes.

Auth-bypass note: unlike the task scaffolding's draft (which proposed
monkeypatching `src.security.auth.get_current_user_id`), this codebase's
actual convention — confirmed against tests/unit/api/test_mcp_servers.py
and tests/unit/api/test_workflow_assignments.py — does NOT monkeypatch that
dependency. Two patterns are used instead:

1. ADR-0015 dev-mode fallback: with no Authorization header and
   ENVIRONMENT=development (the default), `get_current_user_id` returns
   'dev' without any patching needed (see src/security/auth.py).
2. For tests that need a *specific*, non-'dev' identity (e.g. to prove
   ownership checks), issue a real JWT via
   `src.security.jwt.create_access_token(user_id)` and send it as a Bearer
   token — exactly as test_workflow_assignments.py does.

Monkeypatching `src.security.auth.get_current_user_id` would not work here
regardless: `src/api/cloud_storage_oauth.py` does
`from src.security.auth import get_current_user_id` and binds it directly
into `Depends(...)` at router-decoration time (module import, which already
happened by the time `from src.main import create_app` runs at collection).
Patching the attribute on `src.security.auth` afterward does not affect the
already-bound reference the router's `Depends()` captured.
"""

import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _bearer_header(user_id: str) -> dict[str, str]:
    from src.security.jwt import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.cloudstorageconnection = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_authorize_returns_google_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-abc")
    from src.config import get_settings

    get_settings.cache_clear()
    client, _db = _client_with_mock_db()

    # No Authorization header: ADR-0015 dev-mode fallback authenticates as 'dev'.
    resp = client.get("/cloud-storage/google-drive/authorize")
    assert resp.status_code == 200, resp.text
    assert resp.json()["authorizeUrl"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_callback_rejects_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    client, _db = _client_with_mock_db()

    resp = client.get(
        "/cloud-storage/google-drive/callback",
        params={"code": "auth-code", "state": "not-a-real-state"},
    )
    assert resp.status_code == 200  # popup-close HTML, not an HTTP error
    assert "error" in resp.text


def test_callback_success_persists_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drives google_drive_callback all the way through consume_state,
    exchange_code_for_tokens, and the upsert to a 'success' popup-close
    response — this is exactly the path a prior review found was only
    partially covered by the try/except (the upsert sat outside it)."""
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()

    def _fake_consume_state(_state: str) -> str:
        return "user1"

    monkeypatch.setattr("src.api.cloud_storage_oauth.consume_state", _fake_consume_state)
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.exchange_code_for_tokens",
        AsyncMock(
            return_value={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 3600,
                "scope": "drive.readonly drive.metadata",
                "email": "user@example.com",
            }
        ),
    )
    db.cloudstorageconnection.upsert = AsyncMock(
        return_value=SimpleNamespace(id="conn1", userId="user1", accountEmail="user@example.com")
    )

    resp = client.get(
        "/cloud-storage/google-drive/callback",
        params={"code": "auth-code", "state": "irrelevant-because-mocked"},
    )
    assert resp.status_code == 200, resp.text
    assert "success" in resp.text

    db.cloudstorageconnection.upsert.assert_awaited_once()
    call_args = db.cloudstorageconnection.upsert.await_args
    assert call_args is not None
    where = call_args.kwargs["where"]["userId_provider_accountEmail"]
    assert where["provider"] == "google-drive"
    assert where["accountEmail"] == "user@example.com"
    assert where["userId"] == "user1"


def test_list_connections_filters_by_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_many = AsyncMock(
        return_value=[
            SimpleNamespace(id="conn1", provider="google-drive", accountEmail="a@example.com")
        ]
    )

    resp = client.get("/cloud-storage/connections", params={"provider": "google-drive"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"id": "conn1", "provider": "google-drive", "accountEmail": "a@example.com"}
    ]


def test_picker_token_returns_connections_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Google Picker embed (frontend, Task 9) needs the connection's
    current OAuth access token to open — this endpoint hands back the same
    token get_valid_drive_access_token() already produces server-side, for
    one-time client-side use by the Picker widget. Not a new/narrower
    scope: the Picker uses the exact drive.readonly+drive.metadata-scoped
    token already granted by the OAuth flow."""
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="conn1", userId="user1", encryptedAccessToken="enc", expiresAt=None
        )
    )
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.get_valid_drive_access_token",
        AsyncMock(return_value="at-1"),
    )

    resp = client.post(
        "/cloud-storage/connections/conn1/picker-token", headers=_bearer_header("user1")
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accessToken": "at-1"}


def test_picker_token_returns_409_when_reconnect_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pins the fix for a prior review finding: get_picker_token must catch
    DriveTokenExpiredError (raised by get_valid_drive_access_token when the
    token has expired with no refresh token available) and surface it as a
    409, not let it fall through to an unhandled 500 — 409 is what lets the
    Task 9 frontend show a "reconnect your Google Drive" prompt instead of
    a generic error."""
    _set_encryption_key(monkeypatch)
    from src.integrations.google_drive.oauth import DriveTokenExpiredError

    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="conn1", userId="user1", encryptedAccessToken="enc", expiresAt=None
        )
    )
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.get_valid_drive_access_token",
        AsyncMock(side_effect=DriveTokenExpiredError("expired; user must reconnect")),
    )

    resp = client.post(
        "/cloud-storage/connections/conn1/picker-token", headers=_bearer_header("user1")
    )
    assert resp.status_code == 409, resp.text


async def test_picker_token_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-caller bucket for this route is pre-exhausted -> 429, keyed on
    user id — mirrors mcp_servers.py's test_mcp_connection precedent
    (tests/unit/api/test_mcp_servers.py has no route-level 429 test of its
    own, but tests/unit/api/test_users_search.py's test_search_users_rate_limited
    exercises the same enforce()/per_minute_config() call shape)."""
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="conn1", userId="user1", encryptedAccessToken="enc", expiresAt=None
        )
    )
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.get_valid_drive_access_token",
        AsyncMock(return_value="at-1"),
    )

    from typing import cast

    from src.config import get_settings
    from src.security.rate_limit import per_minute_config

    limiter = cast("RateLimiter", client.app.state.rate_limiter)  # type: ignore[attr-defined]
    config = per_minute_config(get_settings().rate_limit_picker_token_per_minute)
    # Drain the bucket for this route+user key before the real request lands.
    for _ in range(config.capacity):
        await limiter.check("picker_token", "user1", config)

    resp = client.post(
        "/cloud-storage/connections/conn1/picker-token", headers=_bearer_header("user1")
    )
    assert resp.status_code == 429, resp.text
    assert "Retry-After" in resp.headers


def test_picker_token_rejects_other_users_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection belongs to the user who created it — another
    authenticated user must not be able to mint a Picker token for it."""
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="conn1", userId="user1", encryptedAccessToken="enc", expiresAt=None
        )
    )

    resp = client.post(
        "/cloud-storage/connections/conn1/picker-token", headers=_bearer_header("user2")
    )
    assert resp.status_code == 404
