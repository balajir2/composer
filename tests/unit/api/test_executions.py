"""Tests for /executions endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from src.main import create_app


def _execution_row(status: str = "running") -> SimpleNamespace:
    return SimpleNamespace(
        id="ex1",
        workflowId="wf1",
        userId="dev",
        status=status,
        currentNodeId=None,
        nodeResults={},
        variables={},
        input="hi",
        output=None,
        error=None,
        startedAt="2026-04-20T00:00:00Z",
        completedAt=None,
        threadId="t1",
    )


def _workflow_row() -> SimpleNamespace:
    """Minimal workflow row that satisfies LangGraphExecutor.run()."""
    return SimpleNamespace(
        id="wf1",
        name="Test Workflow",
        nodes=[
            {"id": "start", "type": "start", "data": {}},
            {"id": "end", "type": "end", "data": {}},
        ],
        edges=[{"source": "start", "target": "end"}],
    )


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=_workflow_row())
    db.workflowexecution = MagicMock()
    db.workflowexecution.create = AsyncMock(return_value=_execution_row())
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="completed"))
    db.workflowexecution.update = AsyncMock(return_value=_execution_row(status="completed"))
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


def test_post_execution_returns_running() -> None:
    client, db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] == "ex1"
    assert body["status"] == "running"
    db.workflowexecution.create.assert_awaited_once()


def test_post_execution_404_when_workflow_missing() -> None:
    client, db = _client_with_mock_db()
    db.workflow.find_unique = AsyncMock(return_value=None)
    resp = client.post("/executions", json={"workflowId": "ghost", "input": "hi"})
    assert resp.status_code == 404


def test_get_execution_returns_row() -> None:
    client, _ = _client_with_mock_db()
    resp = client.get("/executions/ex1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_get_execution_404_when_missing() -> None:
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    resp = client.get("/executions/ghost")
    assert resp.status_code == 404
