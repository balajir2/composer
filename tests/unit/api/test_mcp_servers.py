"""Tests for /mcp-servers REST endpoints."""

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
from tests.unit.db_fakes import FakeSession, FakeSessionmaker, make_user, override_session


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server_row(**overrides: Any) -> McpServerRecord:
    when = datetime(2026, 4, 20, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "srv1",
        "user_id": "dev",
        "name": "Test",
        "url": "https://mcp.example.com/rpc",
        "description": None,
        "category": None,
        "auth_type": "none",
        "encrypted_access_token": None,
        "header_name": None,
        "oauth_config": None,
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


class _RepoMocks:
    """The server repo's functions as AsyncMocks. Assigning an attribute replaces that repo
    function for the rest of the test, so tests can re-script a call mid-way."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        object.__setattr__(self, "_mp", monkeypatch)
        self.create: Any = AsyncMock(return_value=_server_row())
        self.list_visible: Any = AsyncMock(return_value=[_server_row()])
        self.get: Any = AsyncMock(return_value=_server_row())
        self.update: Any = AsyncMock(return_value=_server_row())
        self.delete: Any = AsyncMock(return_value=True)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        object.__getattribute__(self, "_mp").setattr(mcp_servers_repo, name, value)


def _client_with_mock_db(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, _RepoMocks]:
    app = create_app()
    repo = _RepoMocks(monkeypatch)
    # Dev-mode user_id='dev' has no user row by default -> role defaults to 'member'
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=None))
    session = override_session(app)
    app.state.db = MagicMock()  # Prisma: only OAuth token reads still use it
    app.state.db_sessionmaker = FakeSessionmaker(session)
    app.state.session = session
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), repo


def test_post_mcp_server_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    payload = {
        "name": "DeepWiki",
        "url": "https://mcp.deepwiki.com/sse",
        "authType": "none",
    }
    resp = client.post("/mcp-servers", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Test"  # return value of mock
    repo.create.assert_awaited_once()


def test_post_mcp_server_api_key_encrypts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    payload = {
        "name": "Firecrawl",
        "url": "https://mcp.firecrawl.dev/test/v2/sse",
        "authType": "api-key",
        "accessToken": "fc-plain-secret",
        "headerName": "Authorization",
    }
    resp = client.post("/mcp-servers", json=payload)
    assert resp.status_code == 201
    # Verify the CREATE payload's encryptedAccessToken is NOT the plaintext
    call_args = repo.create.await_args
    assert call_args is not None
    created_data = call_args.args[1]
    assert created_data["encrypted_access_token"] != "fc-plain-secret"
    # And it's a non-empty base64-ish string
    assert created_data["encrypted_access_token"]
    assert created_data["auth_type"] == "api-key"


def test_post_mcp_server_unresolvable_url_template_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, _ = _client_with_mock_db(monkeypatch)
    payload = {
        "name": "Bad",
        "url": "https://mcp.example.com/{NONEXISTENT_KEY}/rpc",
        "authType": "none",
    }
    resp = client.post("/mcp-servers", json=payload)
    assert resp.status_code == 422
    assert "NONEXISTENT_KEY" in resp.json()["detail"]


def test_get_mcp_servers_lists_own_and_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    repo.list_visible = AsyncMock(
        return_value=[
            _server_row(id="own", user_id="dev", is_shared=False),
            _server_row(id="shared", user_id="other", is_shared=True),
        ]
    )
    resp = client.get("/mcp-servers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    # Each row returns hasAccessToken as a bool, not the actual token
    for item in body:
        assert "encryptedAccessToken" not in item
        assert "hasAccessToken" in item


def test_post_mcp_server_encrypts_sensitive_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-5: McpServer.headers is returned raw in every read today —
    any secret-looking header value (Authorization, X-Api-Key, ...) must
    be encrypted at rest, same treatment as the workflow node fields."""
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    payload = {
        "name": "Custom",
        "url": "https://mcp.example.com/rpc",
        "authType": "none",
        "headers": {"Authorization": "Bearer plain-secret", "X-Tenant-Id": "acme"},
    }
    resp = client.post("/mcp-servers", json=payload)
    assert resp.status_code == 201, resp.text

    call_args = repo.create.await_args
    created_data = call_args.args[1]  # type: ignore[union-attr]
    stored_headers = created_data["headers"]
    assert stored_headers["Authorization"] != "Bearer plain-secret"
    assert stored_headers["X-Tenant-Id"] == "acme"

    from src.security.encryption import decrypt_marked

    assert decrypt_marked(stored_headers["Authorization"]) == "Bearer plain-secret"


def test_get_mcp_servers_redacts_sensitive_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    from src.security.encryption import encrypt_marked

    client, repo = _client_with_mock_db(monkeypatch)
    repo.list_visible = AsyncMock(
        return_value=[
            _server_row(
                headers={
                    "Authorization": encrypt_marked("Bearer real-secret"),
                    "X-Tenant-Id": "acme",
                }
            ),
        ]
    )
    resp = client.get("/mcp-servers")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    from src.security.encryption import REDACTED_MARKER

    assert body[0]["headers"]["Authorization"] == REDACTED_MARKER
    assert body[0]["headers"]["X-Tenant-Id"] == "acme"
    assert "real-secret" not in resp.text


def test_post_mcp_server_encrypts_oauth_client_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-5: oauthConfig.clientSecret is persisted as plaintext today even
    though McpOAuthToken access/refresh tokens are encrypted — close that
    gap the same way access tokens already are."""
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    payload = {
        "name": "Highspot",
        "url": "https://api.highspot.com/mcp",
        "authType": "oauth",
        "oauthConfig": {
            "authorizeUrl": "https://api.highspot.com/oauth/authorize",
            "tokenUrl": "https://api.highspot.com/oauth/token",
            "clientId": "client-abc",
            "clientSecret": "plain-client-secret",
        },
    }
    resp = client.post("/mcp-servers", json=payload)
    assert resp.status_code == 201, resp.text

    call_args = repo.create.await_args
    created_data = call_args.args[1]  # type: ignore[union-attr]
    stored_config = created_data["oauth_config"]
    assert stored_config["clientSecret"] != "plain-client-secret"

    from src.security.encryption import decrypt_marked

    assert decrypt_marked(stored_config["clientSecret"]) == "plain-client-secret"


def test_post_test_connection_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)

    import src.api.mcp_servers as mcp_servers_api

    class _FakeProvider:
        def __init__(
            self,
            server: Any,
            *,
            db: Any | None = None,
            user_id: str | None = None,
        ) -> None:
            # Accept the db+user_id kwargs the endpoint now passes so
            # OAuth servers can resolve their token.  Static-auth tests
            # ignore both.
            del server, db, user_id

        async def health_check(self) -> Any:
            from src.tools.base import HealthStatus

            return HealthStatus(ok=True, message="healthy, 3 tools")

        async def tools(self) -> list[Any]:
            return []

    monkeypatch.setattr(mcp_servers_api, "McpToolProvider", _FakeProvider)

    resp = client.post("/mcp-servers/srv1/test-connection")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    repo.update.assert_awaited_once()
    assert repo.update.await_args is not None
    updated = repo.update.await_args.args[2]
    assert updated["connection_status"] == "connected"


def test_post_test_connection_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(return_value=None)
    resp = client.post("/mcp-servers/ghost/test-connection")
    assert resp.status_code == 404


def test_delete_owner_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repo = _client_with_mock_db(monkeypatch)
    resp = client.delete("/mcp-servers/srv1")
    assert resp.status_code == 204
    repo.delete.assert_awaited_once()


def test_delete_shared_by_non_owner_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(return_value=_server_row(user_id="someone-else", is_shared=True))
    resp = client.delete("/mcp-servers/srv1")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Phase 9 Task 5 — PATCH /mcp-servers/{id}/owner (admin-only)
# ---------------------------------------------------------------------------


def test_patch_mcp_server_owner_by_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin reassigns MCP server ownership by userId."""
    from src.security.auth import ensure_admin

    client, repo = _client_with_mock_db(monkeypatch)
    target = make_user(id="new-owner", email="target@x.com")
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=target))
    updated = _server_row(user_id="new-owner")
    repo.update.return_value = updated
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    repo.update.assert_awaited_once()
    call_data = repo.update.await_args.args[2]  # type: ignore[union-attr]
    assert call_data["user_id"] == "new-owner"


def test_patch_mcp_server_owner_by_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin reassigns MCP server ownership by email lookup."""
    from src.security.auth import ensure_admin

    client, repo = _client_with_mock_db(monkeypatch)
    target = make_user(id="uid-email", email="target@x.com")
    get_by_email = AsyncMock(return_value=target)
    monkeypatch.setattr(users_repo, "get_by_email", get_by_email)
    updated = _server_row(user_id="uid-email")
    repo.update.return_value = updated
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/owner", json={"email": "target@x.com"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert get_by_email.await_args is not None
    assert get_by_email.await_args.args[1] == "target@x.com"
    repo.update.assert_awaited_once()
    call_data = repo.update.await_args.args[2]  # type: ignore[union-attr]
    assert call_data["user_id"] == "uid-email"


def test_patch_mcp_server_owner_unknown_email_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH with unknown email → 404."""
    from src.security.auth import ensure_admin

    client, _ = _client_with_mock_db(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=None))
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/owner", json={"email": "ghost@x.com"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 404


def test_patch_mcp_server_owner_unknown_server_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH with unknown server → 404."""
    from src.security.auth import ensure_admin

    client, repo = _client_with_mock_db(monkeypatch)
    target = make_user(id="new-owner", email="t@x.com")
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=target))
    repo.get = AsyncMock(return_value=None)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/ghost/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 404


def test_patch_mcp_server_owner_member_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin calling PATCH /owner → 403.

    In test mode the db is a MagicMock (not Prisma), so get_current_role
    returns ('dev', 'member') and ensure_admin raises 403.
    """
    client, _ = _client_with_mock_db(monkeypatch)
    # No admin override → dev-mode 'dev' user → member → ensure_admin raises 403
    resp = client.patch("/mcp-servers/srv1/owner", json={"userId": "new-owner"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Phase 10 Task 16 — PATCH /mcp-servers/{id}/shared (admin-only)
# ---------------------------------------------------------------------------


def test_admin_patch_shared_flips_isshared(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin flips isShared via PATCH /mcp-servers/{id}/shared."""
    from src.security.auth import ensure_admin

    client, repo = _client_with_mock_db(monkeypatch)
    updated = _server_row(is_shared=True)
    repo.update.return_value = updated
    client.app.dependency_overrides[ensure_admin] = lambda: "admin-caller"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/shared", json={"isShared": True})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    body = resp.json()
    assert body["isShared"] is True
    repo.get.assert_awaited()
    call_data = repo.update.await_args.args[2]  # type: ignore[union-attr]
    assert call_data == {"is_shared": True}


def test_member_patch_shared_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin calling PATCH /mcp-servers/{id}/shared → 403."""
    client, _ = _client_with_mock_db(monkeypatch)
    # No admin override → dev-mode 'dev' user → member → ensure_admin raises 403
    resp = client.patch("/mcp-servers/srv1/shared", json={"isShared": True})
    assert resp.status_code == 403


def test_patch_mcp_server_owner_reads_then_updates_then_commits_on_one_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.auth import ensure_admin

    client, repo = _client_with_mock_db(monkeypatch)
    order: list[str] = []

    async def _lookup(*_a: object, **_k: object) -> object:
        order.append("lookup")
        return make_user(id="new-owner")

    async def _get(*_a: object, **_k: object) -> object:
        order.append("get")
        return _server_row()

    async def _update(*_a: object, **_k: object) -> object:
        order.append("update")
        return _server_row(user_id="new-owner")

    async def _commit() -> None:
        order.append("commit")

    monkeypatch.setattr(users_repo, "get_by_id", _lookup)
    client.app.state.session.commit.side_effect = _commit  # type: ignore[attr-defined]
    repo.get = _get
    repo.update = _update
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    assert order == ["lookup", "get", "update", "commit"]


# --- visibility on test-connection -------------------------------------------


def _patch_provider_spy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    import src.api.mcp_servers as mcp_servers_api

    spy = MagicMock()

    class _SpyProvider:
        def __init__(self, server: Any, *, db: Any | None = None, user_id: str | None = None):
            del db, user_id
            spy(server)

        async def health_check(self) -> Any:
            from src.tools.base import HealthStatus

            return HealthStatus(ok=True, message="healthy")

        async def tools(self) -> list[Any]:
            return []

    monkeypatch.setattr(mcp_servers_api, "McpToolProvider", _SpyProvider)
    return spy


def test_test_connection_private_server_of_other_user_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    spy = _patch_provider_spy(monkeypatch)
    repo.get = AsyncMock(
        return_value=_server_row(id="other", user_id="someone-else", is_shared=False)
    )
    resp = client.post("/mcp-servers/other/test-connection")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "MCP server 'other' not found."}
    spy.assert_not_called()
    repo.update.assert_not_awaited()


def test_test_connection_private_404_identical_to_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    repo.get = AsyncMock(
        return_value=_server_row(id="same", user_id="someone-else", is_shared=False)
    )
    private = client.post("/mcp-servers/same/test-connection")
    repo.get = AsyncMock(return_value=None)
    unknown = client.post("/mcp-servers/same/test-connection")
    assert private.status_code == unknown.status_code == 404
    assert private.content == unknown.content


def test_test_connection_shared_server_of_other_user_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    spy = _patch_provider_spy(monkeypatch)
    repo.get = AsyncMock(return_value=_server_row(user_id="someone-else", is_shared=True))
    resp = client.post("/mcp-servers/srv1/test-connection")
    assert resp.status_code == 200
    spy.assert_called_once()
    repo.update.assert_awaited_once()


def test_test_connection_owner_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    spy = _patch_provider_spy(monkeypatch)
    repo.get = AsyncMock(return_value=_server_row(user_id="dev", is_shared=False))
    resp = client.post("/mcp-servers/srv1/test-connection")
    assert resp.status_code == 200
    spy.assert_called_once()
    repo.update.assert_awaited_once()


# --- short-session rule: no session open across the MCP network calls ----------------------


class _RecordingSessionmaker(FakeSessionmaker):
    """FakeSessionmaker that logs session enter/exit into a shared event list."""

    def __init__(self, events: list[str]) -> None:
        super().__init__(FakeSession())
        self._events = events

    def __call__(self) -> "_RecordingSessionmaker._Rec":  # type: ignore[override]
        return _RecordingSessionmaker._Rec(self.session, self._events)

    class _Rec:
        def __init__(self, session: FakeSession, events: list[str]) -> None:
            self._session = session
            self._events = events

        async def __aenter__(self) -> FakeSession:
            self._events.append("session:enter")
            return self._session

        async def __aexit__(self, *exc: object) -> bool:
            self._events.append("session:exit")
            return False


def test_test_connection_holds_no_session_across_network_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, repo = _client_with_mock_db(monkeypatch)
    events: list[str] = []
    client.app.state.db_sessionmaker = _RecordingSessionmaker(events)  # type: ignore[attr-defined]

    async def _get(*_a: object, **_k: object) -> McpServerRecord:
        events.append("db:get")
        return _server_row()

    async def _update(*_a: object, **_k: object) -> McpServerRecord:
        events.append("db:update")
        return _server_row()

    repo.get = _get
    repo.update = _update

    import src.api.mcp_servers as mcp_servers_api

    class _NetworkProvider:
        def __init__(self, server: Any, *, db: Any | None = None, user_id: str | None = None):
            del server, db, user_id

        async def health_check(self) -> Any:
            from src.tools.base import HealthStatus

            events.append("net:health_check")
            return HealthStatus(ok=True, message="healthy")

        async def tools(self) -> list[Any]:
            events.append("net:tools")
            return []

    monkeypatch.setattr(mcp_servers_api, "McpToolProvider", _NetworkProvider)
    resp = client.post("/mcp-servers/srv1/test-connection")
    assert resp.status_code == 200, resp.text

    assert events == [
        "session:enter",
        "db:get",
        "session:exit",
        "net:health_check",
        "net:tools",
        "session:enter",
        "db:update",
        "session:exit",
    ]
    open_sessions = 0
    for event in events:
        if event == "session:enter":
            open_sessions += 1
        elif event == "session:exit":
            open_sessions -= 1
        elif event.startswith("net:"):
            assert open_sessions == 0, f"{event} ran inside an open session"


def test_test_connection_without_sessionmaker_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_mock_db(monkeypatch)
    client.app.state.db_sessionmaker = None  # type: ignore[attr-defined]
    resp = client.post("/mcp-servers/srv1/test-connection")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "database unavailable"}
