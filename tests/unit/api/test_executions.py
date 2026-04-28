"""Tests for /executions endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


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
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
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


def test_delete_execution_owner_succeeds() -> None:
    """Owner can delete their own execution.  Returns 204 with no body
    and cascades to the LangGraph checkpoint tables (writes first,
    then checkpoints, FK ordering)."""
    client, db = _client_with_mock_db()
    db.langgraphcheckpointwrite = MagicMock()
    db.langgraphcheckpointwrite.delete_many = AsyncMock()
    db.langgraphcheckpoint = MagicMock()
    db.langgraphcheckpoint.delete_many = AsyncMock()
    db.workflowexecution.delete = AsyncMock()

    resp = client.delete("/executions/ex1")
    assert resp.status_code == 204
    db.langgraphcheckpointwrite.delete_many.assert_awaited_once_with(where={"threadId": "t1"})
    db.langgraphcheckpoint.delete_many.assert_awaited_once_with(where={"threadId": "t1"})
    db.workflowexecution.delete.assert_awaited_once_with(where={"id": "ex1"})


def test_delete_execution_404_when_missing() -> None:
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    resp = client.delete("/executions/ghost")
    assert resp.status_code == 404


def test_delete_execution_404_when_not_owner() -> None:
    """Non-owner gets 404 (not 403) — same authz pattern as read."""
    client, db = _client_with_mock_db()
    other_user_row = _execution_row()
    other_user_row.userId = "someone-else"
    db.workflowexecution.find_unique = AsyncMock(return_value=other_user_row)
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 404


def test_delete_execution_admin_can_delete_any() -> None:
    """Admin role bypasses ownership check."""
    from src.security.auth import get_current_role

    client, db = _client_with_mock_db()
    other_user_row = _execution_row()
    other_user_row.userId = "someone-else"
    db.workflowexecution.find_unique = AsyncMock(return_value=other_user_row)
    db.langgraphcheckpointwrite = MagicMock()
    db.langgraphcheckpointwrite.delete_many = AsyncMock()
    db.langgraphcheckpoint = MagicMock()
    db.langgraphcheckpoint.delete_many = AsyncMock()
    db.workflowexecution.delete = AsyncMock()

    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.delete("/executions/ex1")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 204
    db.workflowexecution.delete.assert_awaited_once_with(where={"id": "ex1"})


def test_bulk_delete_owned_ids() -> None:
    """Owner can bulk-delete their own executions by id list."""
    client, db = _client_with_mock_db()
    rows = [_execution_row(), _execution_row()]
    rows[1].id = "ex2"
    rows[1].threadId = "t2"
    db.workflowexecution.find_many = AsyncMock(return_value=rows)
    db.workflowexecution.delete_many = AsyncMock()
    db.langgraphcheckpointwrite = MagicMock()
    db.langgraphcheckpointwrite.delete_many = AsyncMock()
    db.langgraphcheckpoint = MagicMock()
    db.langgraphcheckpoint.delete_many = AsyncMock()

    resp = client.post(
        "/executions/delete-bulk",
        json={"executionIds": ["ex1", "ex2"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deletedCount"] == 2
    assert body["skippedCount"] == 0
    db.workflowexecution.delete_many.assert_awaited_once_with(where={"id": {"in": ["ex1", "ex2"]}})


def test_bulk_delete_skips_non_owned_ids() -> None:
    """Member callers passing other users' ids → those rows are
    silently skipped (the find_many WHERE clause filters by userId,
    so they never match)."""
    client, db = _client_with_mock_db()
    # Only ex1 belongs to dev; ex2/ex3 are someone else's and get
    # filtered out by the userId scope.
    db.workflowexecution.find_many = AsyncMock(return_value=[_execution_row()])
    db.workflowexecution.delete_many = AsyncMock()
    db.langgraphcheckpointwrite = MagicMock()
    db.langgraphcheckpointwrite.delete_many = AsyncMock()
    db.langgraphcheckpoint = MagicMock()
    db.langgraphcheckpoint.delete_many = AsyncMock()

    resp = client.post(
        "/executions/delete-bulk",
        json={"executionIds": ["ex1", "ex2", "ex3"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deletedCount"] == 1
    assert body["skippedCount"] == 2


def test_bulk_delete_all_in_scope_member_rejected() -> None:
    """allInScope=true is admin-only — protects members from
    accidentally wiping their own history."""
    client, _ = _client_with_mock_db()
    resp = client.post("/executions/delete-bulk", json={"allInScope": True})
    assert resp.status_code == 403


def test_bulk_delete_all_in_scope_admin_succeeds() -> None:
    """Admin can wipe everything via allInScope=true."""
    from src.security.auth import get_current_role

    client, db = _client_with_mock_db()
    rows = [_execution_row(), _execution_row()]
    rows[1].id = "ex2"
    rows[1].threadId = "t2"
    db.workflowexecution.find_many = AsyncMock(return_value=rows)
    db.workflowexecution.delete_many = AsyncMock()
    db.langgraphcheckpointwrite = MagicMock()
    db.langgraphcheckpointwrite.delete_many = AsyncMock()
    db.langgraphcheckpoint = MagicMock()
    db.langgraphcheckpoint.delete_many = AsyncMock()

    client.app.dependency_overrides[get_current_role] = lambda: ("admin-id", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.post("/executions/delete-bulk", json={"allInScope": True})
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert resp.json()["deletedCount"] == 2


def test_bulk_delete_no_args_rejected() -> None:
    """Empty body → 422.  Don't let a typo at the call site delete
    every execution the caller can see."""
    client, _ = _client_with_mock_db()
    resp = client.post("/executions/delete-bulk", json={})
    assert resp.status_code == 422
