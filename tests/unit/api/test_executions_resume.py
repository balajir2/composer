"""Tests for POST /executions/{id}/resume (Phase 5a)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


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
    from src.engine.events import ExecutionEventBus

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=execution)
    db.workflowexecution.update = AsyncMock(return_value=execution)
    db.approval = MagicMock()
    db.approval.create = AsyncMock()
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
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
    """The endpoint flips status to 'running' BEFORE the background task.
    This prevents polling from seeing stale 'waiting_approval' state."""
    client, db = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200
    assert db.workflowexecution.update.await_count >= 1
    first_update = db.workflowexecution.update.await_args_list[0]
    assert first_update.kwargs["data"]["status"] == "running"


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


def test_resume_does_not_emit_approval_resumed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /resume no longer emits approval-resumed (dropped in DES-007 Phase 9a).

    DES-007 covers the post-resume activity via the subsequent node_started event
    emitted by the LangGraphExecutor after resume() drives the graph.
    """
    import asyncio

    client, _db = _client_with_execution(monkeypatch, _execution_row())
    bus: Any = getattr(client.app, "state").event_bus  # noqa: B009

    async def _collect_after_post() -> list[Any]:
        q = await bus.subscribe("e1")
        events: list[Any] = []
        client.post(
            "/executions/e1/resume",
            json={"decision": "approved", "note": "ok"},
        )
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=0.2)
                except TimeoutError:
                    break
                if ev is None:
                    break
                events.append(ev)
        finally:
            await bus.unsubscribe("e1", q)
        return events

    events = asyncio.run(_collect_after_post())
    # approval-resumed is dropped in DES-007; the bus should have no events
    # from the HTTP layer (only the executor emits workflow_started after resume).
    approval_resumed = [e for e in events if getattr(e, "type", None) == "approval-resumed"]
    assert not approval_resumed, (
        f"unexpected approval-resumed event; got {[(e.type, e.payload) for e in events]}"
    )
