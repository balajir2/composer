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
        userId="dev",
        isPublic=False,
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


def test_post_execution_404_for_other_users_private_workflow() -> None:
    """A member cannot execute a private workflow they do not own."""
    client, db = _client_with_mock_db()
    workflow = _workflow_row()
    workflow.userId = "someone-else"
    db.workflow.find_unique = AsyncMock(return_value=workflow)
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 404
    db.workflowexecution.create.assert_not_awaited()


def test_post_execution_allows_other_users_public_workflow() -> None:
    """Public workflows remain executable by authenticated members."""
    client, db = _client_with_mock_db()
    workflow = _workflow_row()
    workflow.userId = "someone-else"
    workflow.isPublic = True
    db.workflow.find_unique = AsyncMock(return_value=workflow)
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202


def test_post_execution_with_idempotency_key_replays_existing_execution() -> None:
    """A client that retries POST /executions after a network timeout (the
    actual retry vector in this system — LangGraph doesn't auto-retry nodes,
    and the sweeper only marks stuck runs failed, never re-runs them) must
    not get a second execution, or side-effecting nodes downstream (Jira,
    email) would fire twice for one logical request (P1-3)."""
    client, db = _client_with_mock_db()
    existing = _execution_row(status="completed")
    db.workflowexecution.find_unique = AsyncMock(return_value=existing)
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "retry-key-1"},
    )
    assert resp.status_code == 202
    assert resp.json()["id"] == existing.id
    db.workflowexecution.create.assert_not_awaited()


def test_post_execution_with_idempotency_key_creates_when_no_existing_match() -> None:
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "fresh-key"},
    )
    assert resp.status_code == 202
    db.workflowexecution.create.assert_awaited_once()
    _, kwargs = db.workflowexecution.create.call_args
    assert kwargs["data"]["idempotencyKey"] == "fresh-key"


def test_post_execution_without_idempotency_key_skips_lookup() -> None:
    """find_unique still fires once the background run loads the row by id
    (unrelated to idempotency) — assert no call keyed on the composite
    workflowId_idempotencyKey lookup, not "never called at all"."""
    client, db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    lookup_calls = [
        call
        for call in db.workflowexecution.find_unique.await_args_list
        if "workflowId_idempotencyKey" in call.kwargs.get("where", {})
    ]
    assert lookup_calls == []


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
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="completed"))
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


def test_delete_execution_running_rejected_409() -> None:
    """Deleting a still-running execution would delete its LangGraph
    checkpoints out from under the in-flight background task, which then
    fails to persist its final state against a row that no longer exists
    (P1-6). Reject rather than silently deleting live state."""
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="running"))
    db.workflowexecution.delete = AsyncMock()
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 409
    db.workflowexecution.delete.assert_not_awaited()


def test_delete_execution_waiting_approval_rejected_409() -> None:
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=_execution_row(status="waiting_approval")
    )
    db.workflowexecution.delete = AsyncMock()
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 409
    db.workflowexecution.delete.assert_not_awaited()


def test_delete_execution_admin_can_delete_any() -> None:
    """Admin role bypasses ownership check."""
    from src.security.auth import get_current_role

    client, db = _client_with_mock_db()
    other_user_row = _execution_row(status="completed")
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
    rows = [_execution_row(status="completed"), _execution_row(status="completed")]
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


def test_bulk_delete_skips_active_executions() -> None:
    """A running or waiting_approval row in the requested set is skipped,
    not deleted — same reasoning as the single-delete 409 (P1-6): deleting
    live checkpoints out from under an in-flight execution breaks its
    ability to persist a final state."""
    client, db = _client_with_mock_db()
    rows = [
        _execution_row(status="completed"),
        _execution_row(status="running"),
        _execution_row(status="waiting_approval"),
    ]
    rows[1].id, rows[1].threadId = "ex2", "t2"
    rows[2].id, rows[2].threadId = "ex3", "t3"
    db.workflowexecution.find_many = AsyncMock(return_value=rows)
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
    db.workflowexecution.delete_many.assert_awaited_once_with(where={"id": {"in": ["ex1"]}})


def test_bulk_delete_skips_non_owned_ids() -> None:
    """Member callers passing other users' ids → those rows are
    silently skipped (the find_many WHERE clause filters by userId,
    so they never match)."""
    client, db = _client_with_mock_db()
    # Only ex1 belongs to dev; ex2/ex3 are someone else's and get
    # filtered out by the userId scope.
    db.workflowexecution.find_many = AsyncMock(return_value=[_execution_row(status="completed")])
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
    rows = [_execution_row(status="completed"), _execution_row(status="completed")]
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


def test_cancel_execution_running_succeeds() -> None:
    """P1-6: the public status vocabulary documents `canceled`, but nothing
    ever wrote it — worker shutdown persisted `failed` instead, and there
    was no user-triggered cancel operation at all. A running execution can
    now be explicitly canceled."""
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="running"))
    db.workflowexecution.update_many = AsyncMock(return_value=1)
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "canceled"
    where = db.workflowexecution.update_many.call_args.kwargs["where"]
    assert where["id"] == "ex1"
    assert where["status"] == {"in": ["running", "waiting_approval"]}


def test_cancel_execution_waiting_approval_succeeds() -> None:
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=_execution_row(status="waiting_approval")
    )
    db.workflowexecution.update_many = AsyncMock(return_value=1)
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "canceled"


def test_cancel_execution_completed_rejected_409() -> None:
    """Already-terminal executions can't be canceled."""
    client, db = _client_with_mock_db()
    db.workflowexecution.update_many = AsyncMock()
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 409
    db.workflowexecution.update_many.assert_not_awaited()


def test_cancel_execution_404_when_missing() -> None:
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    resp = client.post("/executions/ghost/cancel")
    assert resp.status_code == 404


def test_cancel_execution_404_when_not_owner() -> None:
    client, db = _client_with_mock_db()
    other_user_row = _execution_row(status="running")
    other_user_row.userId = "someone-else"
    db.workflowexecution.find_unique = AsyncMock(return_value=other_user_row)
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 404


def test_cancel_execution_race_loses_when_update_many_affects_zero_rows() -> None:
    """Two near-simultaneous cancel requests (or a cancel racing a resume)
    — only one can win the atomic transition."""
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="running"))
    db.workflowexecution.update_many = AsyncMock(return_value=0)
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 409
