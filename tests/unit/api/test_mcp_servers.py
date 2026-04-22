"""Tests for /mcp-servers REST endpoints."""

import base64
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from src.config import get_settings

    get_settings.cache_clear()


def _server_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "srv1",
        "userId": "dev",
        "name": "Test",
        "url": "https://mcp.example.com/rpc",
        "description": None,
        "category": None,
        "authType": "none",
        "encryptedAccessToken": None,
        "headerName": None,
        "oauthConfig": None,
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
    db.mcpserver.create = AsyncMock(return_value=_server_row())
    db.mcpserver.find_many = AsyncMock(return_value=[_server_row()])
    db.mcpserver.find_unique = AsyncMock(return_value=_server_row())
    db.mcpserver.update = AsyncMock(return_value=_server_row())
    db.mcpserver.delete = AsyncMock()
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_post_mcp_server_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()
    payload = {
        "name": "DeepWiki",
        "url": "https://mcp.deepwiki.com/sse",
        "authType": "none",
    }
    resp = client.post("/mcp-servers", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Test"  # return value of mock
    db.mcpserver.create.assert_awaited_once()


def test_post_mcp_server_api_key_encrypts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()
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
    call_args = db.mcpserver.create.await_args
    assert call_args is not None
    created_data = call_args.kwargs["data"]
    assert created_data["encryptedAccessToken"] != "fc-plain-secret"
    # And it's a non-empty base64-ish string
    assert created_data["encryptedAccessToken"]
    assert created_data["authType"] == "api-key"


def test_post_mcp_server_unresolvable_url_template_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, _ = _client_with_mock_db()
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
    client, db = _client_with_mock_db()
    db.mcpserver.find_many = AsyncMock(
        return_value=[
            _server_row(id="own", userId="dev", isShared=False),
            _server_row(id="shared", userId="other", isShared=True),
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


def test_post_test_connection_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_encryption_key(monkeypatch)
    client, db = _client_with_mock_db()

    import src.api.mcp_servers as mcp_servers_api

    class _FakeProvider:
        def __init__(self, server: Any) -> None:
            pass

        async def health_check(self) -> Any:
            from src.tools.base import HealthStatus

            return HealthStatus(ok=True, message="healthy, 3 tools")

        async def tools(self) -> list[Any]:
            return []

    monkeypatch.setattr(mcp_servers_api, "McpToolProvider", _FakeProvider)

    resp = client.post("/mcp-servers/srv1/test-connection")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    db.mcpserver.update.assert_awaited_once()
    updated = db.mcpserver.update.await_args.kwargs["data"]
    assert updated["connectionStatus"] == "connected"


def test_post_test_connection_not_found() -> None:
    client, db = _client_with_mock_db()
    db.mcpserver.find_unique = AsyncMock(return_value=None)
    resp = client.post("/mcp-servers/ghost/test-connection")
    assert resp.status_code == 404


def test_delete_owner_allowed() -> None:
    client, db = _client_with_mock_db()
    resp = client.delete("/mcp-servers/srv1")
    assert resp.status_code == 204
    db.mcpserver.delete.assert_awaited_once()


def test_delete_shared_by_non_owner_denied() -> None:
    client, db = _client_with_mock_db()
    db.mcpserver.find_unique = AsyncMock(
        return_value=_server_row(userId="someone-else", isShared=True)
    )
    resp = client.delete("/mcp-servers/srv1")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Phase 9 Task 5 — PATCH /mcp-servers/{id}/owner (admin-only)
# ---------------------------------------------------------------------------


def test_patch_mcp_server_owner_by_user_id() -> None:
    """Admin reassigns MCP server ownership by userId."""
    from src.security.auth import ensure_admin

    client, db = _client_with_mock_db()
    target = SimpleNamespace(id="new-owner", email="target@x.com")
    db.user.find_unique = AsyncMock(return_value=target)
    updated = _server_row(userId="new-owner")
    db.mcpserver.update = AsyncMock(return_value=updated)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    db.mcpserver.update.assert_awaited_once()
    call_data = db.mcpserver.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert call_data["userId"] == "new-owner"


def test_patch_mcp_server_owner_by_email() -> None:
    """Admin reassigns MCP server ownership by email lookup."""
    from src.security.auth import ensure_admin

    client, db = _client_with_mock_db()
    target = SimpleNamespace(id="uid-email", email="target@x.com")
    db.user.find_unique = AsyncMock(return_value=target)
    updated = _server_row(userId="uid-email")
    db.mcpserver.update = AsyncMock(return_value=updated)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/owner", json={"email": "target@x.com"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    db.mcpserver.update.assert_awaited_once()
    call_data = db.mcpserver.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert call_data["userId"] == "uid-email"


def test_patch_mcp_server_owner_unknown_email_404() -> None:
    """PATCH with unknown email → 404."""
    from src.security.auth import ensure_admin

    client, db = _client_with_mock_db()
    db.user.find_unique = AsyncMock(return_value=None)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/srv1/owner", json={"email": "ghost@x.com"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 404


def test_patch_mcp_server_owner_unknown_server_404() -> None:
    """PATCH with unknown server → 404."""
    from src.security.auth import ensure_admin

    client, db = _client_with_mock_db()
    target = SimpleNamespace(id="new-owner", email="t@x.com")
    db.user.find_unique = AsyncMock(return_value=target)
    db.mcpserver.find_unique = AsyncMock(return_value=None)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/mcp-servers/ghost/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 404


def test_patch_mcp_server_owner_member_403() -> None:
    """Non-admin calling PATCH /owner → 403.

    In test mode the db is a MagicMock (not Prisma), so get_current_role
    returns ('dev', 'member') and ensure_admin raises 403.
    """
    client, _ = _client_with_mock_db()
    # No admin override → dev-mode 'dev' user → member → ensure_admin raises 403
    resp = client.patch("/mcp-servers/srv1/owner", json={"userId": "new-owner"})
    assert resp.status_code == 403
