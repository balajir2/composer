"""Tests for DELETE /internal/test-users/me (src/api/test_cleanup.py).

Covers the gap this endpoint exists to close: Playwright e2e users
(pw-*@example.com) had no way to be hard-deleted after a test run, so
they and their workflows accumulated forever in the dev database (21 of
them, 2026-07-10 through 2026-07-20, cleaned up manually before this
endpoint existed).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.test_cleanup import router as cleanup_router
from src.db.repos import mcp_oauth as mcp_oauth_repo
from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import users as users_repo
from src.security.jwt import create_access_token
from tests.unit.db_fakes import FakeSession, FakeSessionmaker, make_user, override_session


def _user_row(**overrides: str) -> users_repo.UserRecord:
    return make_user(id="u1", email="pw-7af8e223@example.com", **overrides)


def _client(
    monkeypatch: pytest.MonkeyPatch, *, environment: str = "development"
) -> tuple[TestClient, MagicMock, FakeSession]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", environment)
    from src.config import get_settings

    get_settings.cache_clear()

    app = FastAPI()
    app.include_router(cleanup_router)

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.delete_many = AsyncMock(return_value=0)
    db.workflowassignment = MagicMock()
    db.workflowassignment.delete_many = AsyncMock(return_value=0)
    db.cloudstorageconnection = MagicMock()
    db.cloudstorageconnection.delete_many = AsyncMock(return_value=0)
    app.state.db = db
    session = override_session(app)
    app.state.db_sessionmaker = FakeSessionmaker()  # production fails closed without it
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=_user_row()))
    monkeypatch.setattr(users_repo, "delete", AsyncMock(return_value=True))
    delete_servers = AsyncMock(return_value=0)
    monkeypatch.setattr(mcp_servers_repo, "delete_for_user", delete_servers)
    app.state.delete_mcp_servers = delete_servers
    delete_tokens = AsyncMock(return_value=0)
    delete_states = AsyncMock(return_value=0)
    monkeypatch.setattr(mcp_oauth_repo, "delete_tokens_for_user", delete_tokens)
    monkeypatch.setattr(mcp_oauth_repo, "delete_states_for_user", delete_states)
    app.state.delete_mcp_tokens = delete_tokens
    app.state.delete_mcp_states = delete_states

    return TestClient(app), db, session


def _auth_header(user_id: str = "u1") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_deletes_own_test_account_and_owned_data(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db, session = _client(monkeypatch)
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 204, resp.text

    db.workflow.delete_many.assert_awaited_once_with(where={"userId": "u1"})
    db.workflowassignment.delete_many.assert_awaited_once_with(
        where={"OR": [{"userId": "u1"}, {"assignedById": "u1"}]}
    )
    client.app.state.delete_mcp_servers.assert_awaited_once_with(session, "u1")  # type: ignore[attr-defined]
    client.app.state.delete_mcp_tokens.assert_awaited_once_with(session, "u1")  # type: ignore[attr-defined]
    client.app.state.delete_mcp_states.assert_awaited_once_with(session, "u1")  # type: ignore[attr-defined]
    db.cloudstorageconnection.delete_many.assert_awaited_once_with(where={"userId": "u1"})
    users_repo.delete.assert_awaited_once()  # type: ignore[attr-defined]
    assert users_repo.delete.await_args.args[1] == "u1"  # type: ignore[attr-defined]
    session.commit.assert_awaited_once()


def test_rejects_non_test_email_403(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db, session = _client(monkeypatch)
    monkeypatch.setattr(
        users_repo,
        "get_by_id",
        AsyncMock(return_value=make_user(id="u1", email="alice@realcompany.com")),
    )
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 403
    users_repo.delete.assert_not_awaited()  # type: ignore[attr-defined]
    session.commit.assert_not_awaited()
    db.workflow.delete_many.assert_not_awaited()


def test_404_when_user_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _client(monkeypatch)
    # 1st lookup: the auth dependency's live check (user exists); 2nd: the handler's own.
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(side_effect=[make_user(), None]))
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 404


def test_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense-in-depth: even if this router were included in a production
    app (it isn't -- src/main.py only includes it when environment !=
    'production'), the handler itself refuses to run."""
    client, _, _ = _client(monkeypatch, environment="production")
    resp = client.delete("/internal/test-users/me", headers=_auth_header())
    assert resp.status_code == 404
    # Only the auth dependency's live check looked the user up; the handler itself
    # refused before touching the users repository.
    assert users_repo.get_by_id.await_count == 1  # type: ignore[attr-defined]
    users_repo.delete.assert_not_awaited()  # type: ignore[attr-defined]


def test_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    # environment="test" (not "development"): ADR-0015's no-header→'dev'
    # fallback in get_current_user_id only fires for environment ==
    # "development", so this is the environment that actually exercises
    # the auth requirement rather than the dev bypass.
    client, _, _ = _client(monkeypatch, environment="test")
    resp = client.delete("/internal/test-users/me")
    assert resp.status_code == 401


def test_cannot_target_another_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no user-id parameter at all -- the token's own subject is
    the only account this route can ever act on."""
    client, _, _ = _client(monkeypatch)
    monkeypatch.setattr(
        users_repo,
        "get_by_id",
        AsyncMock(return_value=make_user(id="other-user", email="pw-1@example.com")),
    )
    resp = client.delete("/internal/test-users/me", headers=_auth_header(user_id="other-user"))
    assert resp.status_code == 204
    # find_unique was called with the token's own subject, not any
    # externally supplied id.
    assert users_repo.get_by_id.await_args.args[1] == "other-user"  # type: ignore[attr-defined]
