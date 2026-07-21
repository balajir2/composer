"""Tests for DELETE /internal/test-users/me (src/api/test_cleanup.py).

Covers the gap this endpoint exists to close: Playwright e2e users
(pw-*@example.com) had no way to be hard-deleted after a test run, so
they and their workflows accumulated forever in the dev database (21 of
them, 2026-07-10 through 2026-07-20, cleaned up manually before this
endpoint existed).
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.test_cleanup import router as cleanup_router
from src.security.jwt import create_access_token


def _user_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "u1",
        "email": "pw-7af8e223@example.com",
        "isActive": True,
        "mustChangePassword": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(
    monkeypatch: pytest.MonkeyPatch, *, environment: str = "development"
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", environment)
    from src.config import get_settings

    get_settings.cache_clear()

    app = FastAPI()
    app.include_router(cleanup_router)

    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=_user_row())
    db.user.delete = AsyncMock(return_value=_user_row())
    db.workflow = MagicMock()
    db.workflow.delete_many = AsyncMock(return_value=0)
    db.workflowassignment = MagicMock()
    db.workflowassignment.delete_many = AsyncMock(return_value=0)
    db.mcpserver = MagicMock()
    db.mcpserver.delete_many = AsyncMock(return_value=0)
    db.mcpoauthtoken = MagicMock()
    db.mcpoauthtoken.delete_many = AsyncMock(return_value=0)
    db.mcpoauthstate = MagicMock()
    db.mcpoauthstate.delete_many = AsyncMock(return_value=0)
    db.cloudstorageconnection = MagicMock()
    db.cloudstorageconnection.delete_many = AsyncMock(return_value=0)
    app.state.db = db

    return TestClient(app), db


def _auth_header(user_id: str = "u1") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_deletes_own_test_account_and_owned_data(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 204, resp.text

    db.workflow.delete_many.assert_awaited_once_with(where={"userId": "u1"})
    db.workflowassignment.delete_many.assert_awaited_once_with(
        where={"OR": [{"userId": "u1"}, {"assignedById": "u1"}]}
    )
    db.mcpserver.delete_many.assert_awaited_once_with(where={"userId": "u1"})
    db.mcpoauthtoken.delete_many.assert_awaited_once_with(where={"userId": "u1"})
    db.mcpoauthstate.delete_many.assert_awaited_once_with(where={"userId": "u1"})
    db.cloudstorageconnection.delete_many.assert_awaited_once_with(where={"userId": "u1"})
    db.user.delete.assert_awaited_once_with(where={"id": "u1"})


def test_rejects_non_test_email_403(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row(email="alice@realcompany.com"))
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 403
    db.user.delete.assert_not_awaited()
    db.workflow.delete_many.assert_not_awaited()


def test_404_when_user_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=None)
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 404


def test_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense-in-depth: even if this router were included in a production
    app (it isn't -- src/main.py only includes it when environment !=
    'production'), the handler itself refuses to run."""
    client, db = _client(monkeypatch, environment="production")
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 404
    db.user.find_unique.assert_not_awaited()


def test_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    # environment="test" (not "development"): ADR-0015's no-header→'dev'
    # fallback in get_current_user_id only fires for environment ==
    # "development", so this is the environment that actually exercises
    # the auth requirement rather than the dev bypass.
    client, _ = _client(monkeypatch, environment="test")
    resp = client.delete("/internal/test-users/me")
    assert resp.status_code == 401


def test_cannot_target_another_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no user-id parameter at all -- the token's own subject is
    the only account this route can ever act on."""
    client, db = _client(monkeypatch)
    db.user.find_unique = AsyncMock(return_value=_user_row(id="other-user"))
    resp = client.delete("/internal/test-users/me", headers=_auth_header(user_id="other-user"))
    assert resp.status_code == 204
    # find_unique was called with the token's own subject, not any
    # externally supplied id.
    db.user.find_unique.assert_awaited_once_with(where={"id": "other-user"})
