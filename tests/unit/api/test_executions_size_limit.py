"""Tests for POST /executions input size cap (Phase 8)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _execution_row() -> SimpleNamespace:
    return SimpleNamespace(
        id="e1",
        workflowId="w1",
        userId="dev",
        status="running",
        threadId="t1",
        currentNodeId=None,
        nodeResults={},
        variables={},
        input=None,
        output=None,
        error=None,
        startedAt="2026-04-21T00:00:00Z",
        completedAt=None,
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    workflow_row: Any | None,
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=workflow_row)
    db.workflowexecution = MagicMock()
    db.workflowexecution.create = AsyncMock(return_value=_execution_row())
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row())
    db.workflowexecution.update = AsyncMock(
        return_value=SimpleNamespace(**{**_execution_row().__dict__, "status": "completed"})
    )
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "dev",
        "name": "Test",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {"id": "end", "type": "end", "data": {}},
        ],
        "edges": [{"source": "start", "target": "end"}],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-04-21T00:00:00Z",
        "updatedAt": "2026-04-21T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_execution_input_over_size_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Input > 1 MB → 413."""
    client, _ = _client(monkeypatch, _wf_row())
    huge_input = "x" * 1_500_000
    resp = client.post("/executions", json={"workflowId": "w1", "input": huge_input})
    assert resp.status_code == 413
    assert "max_bytes" in resp.json()["detail"]


def test_execution_input_small_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small input is accepted (passes size check)."""
    client, _ = _client(monkeypatch, _wf_row())
    resp = client.post("/executions", json={"workflowId": "w1", "input": "small"})
    # 202 on happy path; any non-413 proves size check passed
    assert resp.status_code != 413
