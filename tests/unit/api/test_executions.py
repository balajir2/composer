"""Tests for /executions endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


@pytest.fixture(autouse=True)
def _patch_enqueue_execution(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:  # pyright: ignore[reportUnusedFunction]
    """POST /executions now enqueues a real Cloud Task (P1-2) instead of a
    BackgroundTask. Default-patch it to a no-op for every test in this
    module so tests that don't care about enqueueing (most of them) don't
    trip over constructing a real `tasks_v2.CloudTasksAsyncClient()` (which
    resolves ADC credentials this test environment doesn't have). Tests
    that DO care about the enqueue call override this via their own
    `monkeypatch.setattr` on the same fixture-provided monkeypatch
    instance."""
    mock = AsyncMock()
    monkeypatch.setattr("src.api.executions.enqueue_execution", mock)
    return mock


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
    # P1-2: start_execution now creates rows with status='queued' — see
    # LangGraphExecutor.start_execution's docstring; the row only becomes
    # 'running' once /internal/claim-and-run actually claims it.
    db.workflowexecution.create = AsyncMock(return_value=_execution_row(status="queued"))
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="completed"))
    db.workflowexecution.update = AsyncMock(return_value=_execution_row(status="completed"))
    db.executionevent = MagicMock()
    db.executionevent.delete_many = AsyncMock()
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_post_execution_returns_queued() -> None:
    """P1-2: the response now reflects the row's real initial status —
    'queued' — rather than lying about being 'running' before any worker
    has claimed it via /internal/claim-and-run."""
    client, db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] == "ex1"
    assert body["status"] == "queued"
    db.workflowexecution.create.assert_awaited_once()


def test_post_execution_enqueues_cloud_task_instead_of_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 full replacement: POST /executions must enqueue a Cloud Task
    (kind='run') rather than scheduling a request-bound BackgroundTask —
    Cloud Run can scale a request-bound background task's instance to zero
    mid-run, which Cloud Tasks' HTTP-push delivery to
    /internal/claim-and-run is immune to (ADR-0033)."""
    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)

    client, _db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert enqueued == [("ex1", "run")]


def test_post_execution_marks_row_failed_when_enqueue_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Important #2 (P1-2-followup): if enqueue_execution raises after the
    row already committed as 'queued' (un-retried gRPC error, transient
    network blip, IAM/ADC misconfiguration), the row must not be left
    stuck at 'queued' forever — invisible to every sweeper (they only
    scan status='running') and, if the caller retries with the same
    idempotencyKey, permanently un-repairable (the idempotency
    short-circuit would just keep returning the same stuck row without
    ever calling enqueue_execution again). The endpoint must mark the row
    'failed' and return a handled error, not an unhandled 500.

    The mark-failed write must be an atomic, status-guarded `update_many`
    (where status='queued'), matching the pattern used everywhere else in
    this file — NOT an unconditional `update()` — so a concurrent cancel
    that already flipped the row to 'canceled' can't be clobbered back to
    'failed' by a stale enqueue error (see the dedicated race test below).
    """

    async def _raise_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        raise RuntimeError("Cloud Tasks unavailable")

    monkeypatch.setattr("src.api.executions.enqueue_execution", _raise_enqueue)

    client, db = _client_with_mock_db()
    db.workflowexecution.update_many = AsyncMock(return_value=1)
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 500
    assert "ex1" in resp.json()["detail"]

    db.workflowexecution.update_many.assert_awaited_once()
    _, kwargs = db.workflowexecution.update_many.call_args
    assert kwargs["where"] == {"id": "ex1", "status": "queued"}
    assert kwargs["data"]["status"] == "failed"
    assert "enqueue" in kwargs["data"]["error"]
    db.workflowexecution.update.assert_not_awaited()


def test_post_execution_enqueue_failure_does_not_clobber_concurrent_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue 2 regression: a client cancels the still-'queued' row (now
    valid, P1-2-followup) while enqueue_execution is mid-flight/timing
    out. cancel_execution's atomic update_many wins first (row ->
    'canceled'). When the enqueue-failure handler then tries to mark the
    row 'failed', its status-guarded update_many (where status='queued')
    must affect zero rows — since the row is no longer 'queued' — so it
    becomes a safe no-op instead of clobbering the legitimate
    cancellation. The caller still gets an informative 500 for the
    enqueue failure itself; the row's real status is left untouched."""

    async def _raise_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        raise RuntimeError("Cloud Tasks unavailable")

    monkeypatch.setattr("src.api.executions.enqueue_execution", _raise_enqueue)

    client, db = _client_with_mock_db()
    # 0 rows affected == exactly what Postgres would report if a
    # concurrent cancel already flipped this row's status away from
    # 'queued' before this update_many's WHERE clause evaluated.
    db.workflowexecution.update_many = AsyncMock(return_value=0)
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 500
    assert "ex1" in resp.json()["detail"]

    db.workflowexecution.update_many.assert_awaited_once()
    _, kwargs = db.workflowexecution.update_many.call_args
    # The guard is what makes this a no-op against an already-canceled
    # row — asserting it's present is the actual regression check.
    assert kwargs["where"] == {"id": "ex1", "status": "queued"}
    db.workflowexecution.update.assert_not_awaited()


def test_post_execution_enqueue_failure_survives_nested_mark_failed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue 3 regression: if the mark-failed `update_many` call itself
    raises (a DB blip right after the row's own successful create()), that
    secondary exception must NOT propagate and mask the original,
    informative enqueue-failure HTTPException with FastAPI's generic 500
    handler. The client must still receive the same handled 500 response
    referencing the execution id, proving the primary error survives the
    nested failure."""

    async def _raise_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        raise RuntimeError("Cloud Tasks unavailable")

    monkeypatch.setattr("src.api.executions.enqueue_execution", _raise_enqueue)

    client, db = _client_with_mock_db()
    db.workflowexecution.update_many = AsyncMock(side_effect=RuntimeError("DB blip"))
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 500
    body = resp.json()
    assert "ex1" in body["detail"]
    assert "enqueued" in body["detail"]
    db.workflowexecution.update_many.assert_awaited_once()


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
    """Assert no find_unique call keyed on the composite
    workflowId_idempotencyKey lookup — not "never called at all", since
    other find_unique calls unrelated to idempotency (e.g. workflow
    lookups) are legitimate. As of P1-2, the row is no longer run
    in-process (Cloud Tasks enqueueing replaces the BackgroundTask), so
    there is no longer a background `executor.run()` call in this test
    to also trigger a lookup by id."""
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
    then checkpoints, FK ordering) and to execution_events (P1-4)."""
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
    db.executionevent.delete_many.assert_awaited_once_with(where={"executionId": "ex1"})
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


def test_delete_execution_queued_rejected_409() -> None:
    """Critical regression (P1-2-followup): a 'queued' row may be actively
    in flight to Cloud Tasks, or already claimed and running by a
    concurrent /internal/claim-and-run delivery — it is never safe to
    delete unconditionally. Must behave exactly like 'running': 409, no
    delete call."""
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="queued"))
    db.workflowexecution.delete = AsyncMock()
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 409
    db.workflowexecution.delete.assert_not_awaited()


def test_bulk_delete_skips_queued_executions() -> None:
    """Same guard as the single-delete 409 above, applied to the bulk path."""
    client, db = _client_with_mock_db()
    rows = [_execution_row(status="completed"), _execution_row(status="queued")]
    rows[1].id, rows[1].threadId = "ex2", "t2"
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
    assert body["deletedCount"] == 1
    assert body["skippedCount"] == 1
    db.workflowexecution.delete_many.assert_awaited_once_with(where={"id": {"in": ["ex1"]}})


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
    db.executionevent.delete_many.assert_awaited_once_with(
        where={"executionId": {"in": ["ex1", "ex2"]}}
    )
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
    # P1-2-followup: 'queued' is now a cancellable status too (see
    # _ACTIVE_EXECUTION_STATUSES_SORTED) — the atomic update_many's
    # allow-list is sourced from the same set as the guard check above it.
    assert where["status"] == {"in": ["queued", "running", "waiting_approval"]}


def test_cancel_execution_queued_succeeds() -> None:
    """Important #1 (P1-2-followup): a user who cancels immediately after
    starting an execution — before any worker has claimed it — must not
    get an incorrect 409. Canceling a 'queued' row is safe on its own:
    claim-and-run's claim query only matches
    status IN ('queued', 'running', 'waiting_approval'), so once this
    flips the row to 'canceled' the eventual Cloud Task delivery finds
    nothing to claim (no separate task-cancellation call needed)."""
    client, db = _client_with_mock_db()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="queued"))
    db.workflowexecution.update_many = AsyncMock(return_value=1)
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "canceled"
    where = db.workflowexecution.update_many.call_args.kwargs["where"]
    assert where["status"] == {"in": ["queued", "running", "waiting_approval"]}


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
