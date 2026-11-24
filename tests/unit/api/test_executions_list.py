"""Tests for GET /executions (list) — Phase 7b."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import executions as executions_repo
from src.db.repos import users as users_repo
from src.db.repos.executions import ExecutionRecord
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSessionmaker, make_user


def _exec_row(**overrides: Any) -> ExecutionRecord:
    base: dict[str, Any] = {
        "id": "e1",
        "workflow_id": "w1",
        "user_id": "dev",
        "status": "completed",
        "current_node_id": None,
        "node_results": {},
        "variables": {},
        "input": None,
        "output": None,
        "error": None,
        "failure_code": None,
        "started_at": datetime(2026, 4, 21, tzinfo=UTC),
        "completed_at": datetime(2026, 4, 21, 0, 0, 10, tzinfo=UTC),
        "thread_id": "t1",
        "original_owner_email": None,
        "idempotency_key": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "running_since": None,
        "delivery_attempts": 0,
    }
    base.update(overrides)
    return ExecutionRecord(**base)


def _client(monkeypatch: pytest.MonkeyPatch, rows: list[Any], total: int) -> tuple[TestClient, Any]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    list_page = AsyncMock(return_value=(rows, total))
    monkeypatch.setattr(executions_repo, "list_page", list_page)
    monkeypatch.setattr(executions_repo, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="dev")))

    db = MagicMock()
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.db_sessionmaker = FakeSessionmaker()
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), list_page


def test_list_executions_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [_exec_row(), _exec_row(id="e2")], total=2)
    resp = client.get("/executions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_executions_filter_by_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client, list_page = _client(monkeypatch, [_exec_row(workflow_id="wX")], total=1)
    resp = client.get("/executions?workflowId=wX")
    assert resp.status_code == 200
    assert list_page.await_args.kwargs["workflow_id"] == "wX"


def test_list_executions_filter_by_status(monkeypatch: pytest.MonkeyPatch) -> None:
    client, list_page = _client(monkeypatch, [_exec_row(status="failed")], total=1)
    resp = client.get("/executions?status=failed")
    assert resp.status_code == 200
    assert list_page.await_args.kwargs["status"] == "failed"


def test_list_executions_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    client, list_page = _client(monkeypatch, [], total=100)
    resp = client.get("/executions?limit=10&offset=20")
    assert resp.status_code == 200
    assert list_page.await_args.kwargs["limit"] == 10
    assert list_page.await_args.kwargs["offset"] == 20
    body = resp.json()
    assert body["limit"] == 10
    assert body["offset"] == 20


def test_list_executions_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/executions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_list_executions_scopes_to_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without any filter, list always scopes to the caller's own executions."""
    client, list_page = _client(monkeypatch, [], total=0)
    resp = client.get("/executions")
    assert resp.status_code == 200
    assert list_page.await_args.kwargs["owner_id"] == "dev"  # dev-mode fallback caller


def test_list_executions_limit_over_100_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client, list_page = _client(monkeypatch, [], total=0)
    assert client.get("/executions?limit=101").status_code == 422
    list_page.assert_not_awaited()


# ---------------------------------------------------------------------------
# Phase 9 Task 5 — Admin role bypass on executions list
# ---------------------------------------------------------------------------


def test_admin_list_executions_sees_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin bypasses the userId filter and sees all executions."""
    from src.security.auth import get_current_role

    other_exec = _exec_row(id="e-other", user_id="other-user")
    client, list_page = _client(monkeypatch, [other_exec], total=1)
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.get("/executions")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    # Admin — no owner filter
    assert list_page.await_args.kwargs["owner_id"] is None


def test_admin_get_execution_for_other_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin can GET an execution belonging to another user."""
    from src.security.auth import get_current_role

    other_exec = _exec_row(id="e-other", user_id="other-user")
    client, _ = _client(monkeypatch, [], total=0)
    monkeypatch.setattr(executions_repo, "get", AsyncMock(return_value=other_exec))
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.get("/executions/e-other")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200


@pytest.mark.parametrize("failure_code", ["execution_timeout", None])
def test_list_serializes_failure_code(
    monkeypatch: pytest.MonkeyPatch, failure_code: str | None
) -> None:
    client, _ = _client(monkeypatch, [_exec_row(failure_code=failure_code)], total=1)
    response = cast("Any", client).get("/executions")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["failureCode"] == failure_code
