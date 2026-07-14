"""Tests for POST /executions/{id}/resume (Phase 5a)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.engine.events import ExecutionEvent
from src.main import create_app
from src.security.rate_limit import RateLimiter


@pytest.fixture(autouse=True)
def _patch_enqueue_execution(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """POST /executions/{id}/resume now enqueues a real Cloud Task (P1-2)
    instead of a BackgroundTask. Default-patch it to a no-op for every
    test in this module so tests that don't care about enqueueing (most
    of them) don't trip over constructing a real
    `tasks_v2.CloudTasksAsyncClient()` (which resolves ADC credentials
    this test environment doesn't have). Tests that DO care about the
    enqueue call override this via their own `monkeypatch.setattr` on the
    same fixture-provided monkeypatch instance — mirrors
    tests/unit/api/test_executions.py's identical fixture."""
    mock = AsyncMock()
    monkeypatch.setattr("src.api.executions.enqueue_execution", mock)
    return mock


class _FakeEventStore:
    """In-memory stand-in for PostgresEventStore.append — the background
    resume task calls event_bus.append(...); a real ExecutionEventBus has
    no such method and NOTIFY needs a live Postgres connection, neither of
    which this unit test has."""

    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    async def append(self, event: ExecutionEvent) -> int:
        seq = len(self.events) + 1
        self.events.append(event)
        return seq


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "e1",
        "workflowId": "w1",
        "userId": "dev",
        "status": "waiting_approval",
        "threadId": "t1",
        "nodeResults": {},
        "variables": {
            "_pending_approval_node": "ua",
            "_pending_approval_prompt": "Approve?",
        },
        "input": None,
        "output": None,
        "error": None,
        "currentNodeId": None,
        "startedAt": "2026-04-21T00:00:00Z",
        "completedAt": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client_with_execution(
    monkeypatch: pytest.MonkeyPatch,
    execution: Any | None,
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=execution)
    db.workflowexecution.update = AsyncMock(return_value=execution)
    db.workflowexecution.update_many = AsyncMock(return_value=1)
    db.approval = MagicMock()
    db.approval.create = AsyncMock()
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.event_bus = _FakeEventStore()
    app.state.rate_limiter = RateLimiter()
    monkeypatch.setattr("src.engine.langgraph_executor.notify_execution_event", AsyncMock())
    return TestClient(app), db


def test_resume_approved_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "approved", "note": "lgtm"},
    )
    assert resp.status_code == 200, resp.text
    db.approval.create.assert_awaited_once()
    call = db.approval.create.await_args
    created = call.kwargs["data"]
    assert created["decision"] == "approved"
    assert created["note"] == "lgtm"
    assert created["nodeId"] == "ua"
    assert created["executionId"] == "e1"
    assert created["approverUserId"] == "dev"  # dev-mode fallback


def test_resume_rejected_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "rejected"},
    )
    assert resp.status_code == 200
    call = db.approval.create.await_args
    created = call.kwargs["data"]
    assert created["decision"] == "rejected"
    assert created["note"] is None


def test_resume_execution_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_execution(monkeypatch, None)
    resp = client.post(
        "/executions/ghost/resume",
        json={"decision": "approved"},
    )
    assert resp.status_code == 404


def test_resume_wrong_status_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_execution(monkeypatch, _execution_row(status="completed"))
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "approved"},
    )
    assert resp.status_code == 409


def test_resume_missing_pending_node_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """Execution is waiting_approval but variables don't have _pending_approval_node."""
    client, _ = _client_with_execution(monkeypatch, _execution_row(variables={}))
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "approved"},
    )
    assert resp.status_code == 500


def test_resume_invalid_decision_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "maybe"},
    )
    assert resp.status_code == 422


def test_resume_marks_execution_running_before_scheduling_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint flips status to 'running' via the atomic conditional
    update_many BEFORE the background task, and returns a response that
    reflects the transition (not the stale pre-transition row)."""
    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    db.workflowexecution.update_many.assert_awaited_once()
    call = db.workflowexecution.update_many.await_args
    assert call.kwargs["data"]["status"] == "running"
    assert call.kwargs["where"]["status"] == "waiting_approval"
    assert resp.json()["status"] == "running"


def test_resume_race_loses_when_update_many_affects_zero_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-1: the atomic waiting_approval -> running transition is the real
    concurrency guard. If a concurrent request already flipped the row
    (update_many affects 0 rows), this request must not create a second
    audit row or schedule a second resumption — 409, no side effects."""
    client, db = _client_with_execution(monkeypatch, _execution_row())
    db.workflowexecution.update_many = AsyncMock(return_value=0)
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 409
    db.approval.create.assert_not_awaited()


def test_get_execution_non_owner_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner reading an execution gets 404 (info-leak tight)."""
    client, _ = _client_with_execution(monkeypatch, _execution_row(id="e1", userId="someone-else"))
    resp = client.get("/executions/e1")
    assert resp.status_code == 404


def test_resume_non_owner_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-owner resuming an execution gets 404 (info-leak tight)."""
    client, _ = _client_with_execution(
        monkeypatch,
        _execution_row(id="e1", userId="someone-else", status="waiting_approval"),
    )
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 9 Task 5 — Admin role bypass on resume
# ---------------------------------------------------------------------------


def test_admin_can_resume_other_users_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin can resume an execution belonging to another user."""
    from src.security.auth import get_current_role

    execution = _execution_row(userId="someone-else")
    client, db = _client_with_execution(monkeypatch, execution)
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    db.approval.create.assert_awaited_once()


def test_resume_enqueues_cloud_task_instead_of_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 full replacement: POST /executions/{id}/resume must enqueue a
    Cloud Task (kind='resume') rather than scheduling a request-bound
    BackgroundTask — mirrors POST /executions' own Task 12 replacement.
    The decision is stamped into `variables._resume_decision` BEFORE
    enqueueing since enqueue_execution's Cloud Task body is hardcoded to
    {"executionId": ..., "kind": ...} with no room for the decision value;
    claim-and-run reads it back from the claimed row's variables."""
    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)

    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    assert enqueued == [("e1", "resume")]

    db.workflowexecution.update.assert_awaited_once()
    update_call = db.workflowexecution.update.await_args
    assert update_call.kwargs["where"]["id"] == "e1"
    stamped_variables = update_call.kwargs["data"]["variables"].data
    assert stamped_variables["_resume_decision"] == "approved"
    # Original variables (the pending-approval bookkeeping) must survive
    # the stamp, not be clobbered.
    assert stamped_variables["_pending_approval_node"] == "ua"


def test_resume_does_not_emit_approval_resumed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /resume no longer emits approval-resumed (dropped in DES-007 Phase 9a).

    DES-007 covers the post-resume activity via the subsequent node_started event
    emitted by the LangGraphExecutor after resume() drives the graph.
    """
    client, _db = _client_with_execution(monkeypatch, _execution_row())
    store: _FakeEventStore = getattr(client.app, "state").event_bus  # noqa: B009

    client.post(
        "/executions/e1/resume",
        json={"decision": "approved", "note": "ok"},
    )

    # approval-resumed is dropped in DES-007; the persisted store should have
    # no events of that type (only the executor's own workflow_started /
    # node_started / etc. events land here after resume() drives the graph).
    approval_resumed = [e for e in store.events if e.type == "approval-resumed"]  # pyright: ignore[reportUnnecessaryComparison]
    assert not approval_resumed, (
        f"unexpected approval-resumed event; got {[(e.type, e.payload) for e in store.events]}"
    )
