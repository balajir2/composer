"""Tests for GET /executions (list) — Phase 7b."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _exec_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "e1",
        "workflowId": "w1",
        "userId": "dev",
        "status": "completed",
        "currentNodeId": None,
        "nodeResults": {},
        "variables": {},
        "input": None,
        "output": None,
        "error": None,
        "startedAt": "2026-04-21T00:00:00Z",
        "completedAt": "2026-04-21T00:00:10Z",
        "threadId": "t1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(
    monkeypatch: pytest.MonkeyPatch, rows: list[Any], total: int
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.count = AsyncMock(return_value=total)
    db.workflowexecution.find_many = AsyncMock(return_value=rows)
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_list_executions_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [_exec_row(), _exec_row(id="e2")], total=2)
    resp = client.get("/executions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_executions_filter_by_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_exec_row(workflowId="wX")], total=1)
    resp = client.get("/executions?workflowId=wX")
    assert resp.status_code == 200
    where = db.workflowexecution.find_many.await_args.kwargs["where"]
    assert where["workflowId"] == "wX"


def test_list_executions_filter_by_status(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_exec_row(status="failed")], total=1)
    resp = client.get("/executions?status=failed")
    assert resp.status_code == 200
    where = db.workflowexecution.find_many.await_args.kwargs["where"]
    assert where["status"] == "failed"


def test_list_executions_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=100)
    resp = client.get("/executions?limit=10&offset=20")
    assert resp.status_code == 200
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
    client, db = _client(monkeypatch, [], total=0)
    resp = client.get("/executions")
    assert resp.status_code == 200
    where = db.workflowexecution.find_many.await_args.kwargs["where"]
    assert where["userId"] == "dev"  # dev-mode fallback caller


# ---------------------------------------------------------------------------
# Phase 9 Task 5 — Admin role bypass on executions list
# ---------------------------------------------------------------------------


def test_admin_list_executions_sees_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin bypasses the userId filter and sees all executions."""
    from src.security.auth import get_current_role

    other_exec = _exec_row(id="e-other", userId="other-user")
    client, db = _client(monkeypatch, [other_exec], total=1)
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.get("/executions")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    where = db.workflowexecution.find_many.await_args.kwargs["where"]
    # Admin — no userId filter in where clause
    assert "userId" not in where


def test_admin_get_execution_for_other_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin can GET an execution belonging to another user."""
    from src.security.auth import get_current_role

    other_exec = _exec_row(id="e-other", userId="other-user")
    client, db = _client(monkeypatch, [], total=0)
    db.workflowexecution.find_unique = AsyncMock(return_value=other_exec)
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.get("/executions/e-other")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
