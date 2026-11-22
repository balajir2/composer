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
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import cloud_storage as cloud_storage_repo
from src.db.repos import users as users_repo
from src.db.repos.cloud_storage import CloudStorageConnectionRecord
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session, make_user


def _connection(**overrides: Any) -> CloudStorageConnectionRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "conn1",
        "user_id": "user1",
        "provider": "google-drive",
        "account_email": "a@example.com",
        "encrypted_access_token": "enc",
        "encrypted_refresh_token": None,
        "expires_at": None,
        "scope": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return CloudStorageConnectionRecord(**base)


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _bearer_header(user_id: str) -> dict[str, str]:
    from src.security.jwt import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _consume_user1(_state: str) -> str:
    return "user1"


def _client_with_mock_db(
    monkeypatch: pytest.MonkeyPatch, events: list[str] | None = None
) -> tuple[TestClient, RecordingSessionmaker]:
    """App whose routes open sessions from a recording sessionmaker; repo calls are patched."""
    app = create_app()
    sessionmaker = RecordingSessionmaker(events if events is not None else [])
    app.state.db_sessionmaker = sessionmaker

    async def _live_user(_session: Any, user_id: str) -> Any:
        return make_user(id=user_id)  # bearer callers must still exist (live user check)

    monkeypatch.setattr(users_repo, "get_by_id", _live_user)
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), sessionmaker


def test_authorize_returns_google_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-abc")
    from src.config import get_settings

    get_settings.cache_clear()
    client, _sm = _client_with_mock_db(monkeypatch)

    # No Authorization header: ADR-0015 dev-mode fallback authenticates as 'dev'.
    resp = client.get("/cloud-storage/google-drive/authorize")
    assert resp.status_code == 200, resp.text
    assert resp.json()["authorizeUrl"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_callback_rejects_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    client, _sm = _client_with_mock_db(monkeypatch)

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
    client, sm = _client_with_mock_db(monkeypatch)

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
    upsert = AsyncMock(return_value=_connection(account_email="user@example.com"))
    monkeypatch.setattr(cloud_storage_repo, "upsert", upsert)

    resp = client.get(
        "/cloud-storage/google-drive/callback",
        params={"code": "auth-code", "state": "irrelevant-because-mocked"},
    )
    assert resp.status_code == 200, resp.text
    assert "success" in resp.text

    upsert.assert_awaited_once()
    assert upsert.await_args is not None
    kwargs = upsert.await_args.kwargs
    assert kwargs["provider"] == "google-drive"
    assert kwargs["account_email"] == "user@example.com"
    assert kwargs["user_id"] == "user1"
    values = kwargs["values"]
    assert values["encrypted_refresh_token"] is not None
    assert values["scope"] == "drive.readonly drive.metadata"
    sm.session.commit.assert_awaited_once()


def test_list_connections_filters_by_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    client, _sm = _client_with_mock_db(monkeypatch)
    listing = AsyncMock(return_value=[_connection()])
    monkeypatch.setattr(cloud_storage_repo, "list_for_user", listing)

    resp = client.get("/cloud-storage/connections", params={"provider": "google-drive"})
    assert resp.status_code == 200, resp.text
    # Only id/provider/accountEmail: never token material.
    assert resp.json() == [
        {"id": "conn1", "provider": "google-drive", "accountEmail": "a@example.com"}
    ]
    assert listing.await_args is not None
    assert listing.await_args.args[1:] == ("dev", "google-drive")


def test_picker_token_returns_connections_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Google Picker embed (frontend, Task 9) needs the connection's
    current OAuth access token to open — this endpoint hands back the same
    token get_valid_drive_access_token() already produces server-side, for
    one-time client-side use by the Picker widget. Not a new/narrower
    scope: the Picker uses the exact drive.readonly+drive.metadata-scoped
    token already granted by the OAuth flow."""
    _set_encryption_key(monkeypatch)
    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(return_value=_connection()))
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

    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(return_value=_connection()))
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
    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(return_value=_connection()))
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
    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(return_value=_connection()))

    resp = client.post(
        "/cloud-storage/connections/conn1/picker-token", headers=_bearer_header("user2")
    )
    assert resp.status_code == 404


def test_picker_token_unknown_connection_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(return_value=None))
    resp = client.post(
        "/cloud-storage/connections/nope/picker-token", headers=_bearer_header("user1")
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Connection not found."}


def test_picker_token_closes_its_session_before_fetching_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short-session rule: the ownership read is closed before get_valid_drive_access_token
    (which may call Google) runs."""
    _set_encryption_key(monkeypatch)
    events: list[str] = []
    client, sm = _client_with_mock_db(monkeypatch, events)

    async def _get(_session: Any, _connection_id: str) -> CloudStorageConnectionRecord:
        events.append("db:get")
        return _connection()

    async def _token(_connection_id: str, passed_sm: Any) -> str:
        events.append("net:token")
        assert passed_sm is sm
        return "at-1"

    monkeypatch.setattr(cloud_storage_repo, "get", _get)
    monkeypatch.setattr("src.api.cloud_storage_oauth.get_valid_drive_access_token", _token)

    resp = client.post(
        "/cloud-storage/connections/conn1/picker-token", headers=_bearer_header("user1")
    )
    assert resp.status_code == 200, resp.text
    # (The auth dependency's live user check opens its own session earlier.)
    assert events[-4:] == ["session:enter", "db:get", "session:exit", "net:token"]
    assert_no_network_inside_session(events)


def test_callback_upserts_only_after_the_google_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short-session rule: no session is open during the token exchange / userinfo calls; the
    upsert gets its own session afterwards."""
    _set_encryption_key(monkeypatch)
    events: list[str] = []
    client, _sm = _client_with_mock_db(monkeypatch, events)
    monkeypatch.setattr("src.api.cloud_storage_oauth.consume_state", _consume_user1)

    async def _exchange(_code: str, _redirect: str) -> dict[str, Any]:
        events.append("net:exchange")
        return {"access_token": "at-1", "expires_in": 3600, "email": "user@example.com"}

    async def _upsert(_session: Any, **_kwargs: Any) -> CloudStorageConnectionRecord:
        events.append("db:upsert")
        return _connection()

    monkeypatch.setattr("src.api.cloud_storage_oauth.exchange_code_for_tokens", _exchange)
    monkeypatch.setattr(cloud_storage_repo, "upsert", _upsert)

    resp = client.get("/cloud-storage/google-drive/callback", params={"code": "c", "state": "s"})
    assert resp.status_code == 200 and "success" in resp.text
    assert events == ["net:exchange", "session:enter", "db:upsert", "session:exit"]  # no auth dep
    assert_no_network_inside_session(events)


def test_callback_without_refresh_token_stores_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinned like-for-like: a re-grant without a refresh_token writes NULL refresh token and
    keeps whatever scope Google returned (None here)."""
    _set_encryption_key(monkeypatch)
    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr("src.api.cloud_storage_oauth.consume_state", _consume_user1)
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.exchange_code_for_tokens",
        AsyncMock(return_value={"access_token": "at-1", "email": "user@example.com"}),
    )
    upsert = AsyncMock(return_value=_connection())
    monkeypatch.setattr(cloud_storage_repo, "upsert", upsert)

    resp = client.get("/cloud-storage/google-drive/callback", params={"code": "c", "state": "s"})
    assert resp.status_code == 200 and "success" in resp.text
    assert upsert.await_args is not None
    values = upsert.await_args.kwargs["values"]
    assert values["encrypted_refresh_token"] is None and values["scope"] is None


def test_callback_persist_failure_is_a_friendly_error_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr("src.api.cloud_storage_oauth.consume_state", _consume_user1)
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.exchange_code_for_tokens",
        AsyncMock(return_value={"access_token": "at-1", "email": "user@example.com"}),
    )
    monkeypatch.setattr(cloud_storage_repo, "upsert", AsyncMock(side_effect=RuntimeError("boom")))

    resp = client.get("/cloud-storage/google-drive/callback", params={"code": "c", "state": "s"})
    assert resp.status_code == 200
    assert "error" in resp.text and "boom" not in resp.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/cloud-storage/connections"),
        ("post", "/cloud-storage/connections/conn1/picker-token"),
    ],
)
def test_routes_fail_closed_without_a_database(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    _set_encryption_key(monkeypatch)
    app = create_app()
    app.state.rate_limiter = RateLimiter()
    client = TestClient(app)
    resp = getattr(client, method)(path, headers=_bearer_header("user1"))
    assert resp.status_code == 503
    assert resp.json() == {"detail": "database unavailable"}


def test_callback_without_a_database_closes_the_popup_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The popup waits for the postMessage; a JSON 503 would leave it hanging."""
    _set_encryption_key(monkeypatch)
    app = create_app()
    app.state.rate_limiter = RateLimiter()
    client = TestClient(app)
    resp = client.get("/cloud-storage/google-drive/callback", params={"code": "c", "state": "s"})
    assert resp.status_code == 200
    assert "window.close()" in resp.text
    assert '"status": "error"' in resp.text and "database unavailable" in resp.text


def test_picker_token_connection_vanished_mid_refresh_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_picker_token maps DriveConnectionMissingError (row deleted between the ownership
    read and the refresh write) to 404 with the helper's message."""
    from src.integrations.google_drive.oauth import DriveConnectionMissingError

    _set_encryption_key(monkeypatch)
    client, _sm = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(return_value=_connection()))
    monkeypatch.setattr(
        "src.api.cloud_storage_oauth.get_valid_drive_access_token",
        AsyncMock(
            side_effect=DriveConnectionMissingError(
                "No CloudStorageConnection found for id 'conn1'."
            )
        ),
    )
    resp = client.post(
        "/cloud-storage/connections/conn1/picker-token", headers=_bearer_header("user1")
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "No CloudStorageConnection found for id 'conn1'."}
