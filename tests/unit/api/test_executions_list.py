"""Tests for GET /executions (list) — Phase 7b."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


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
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
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
