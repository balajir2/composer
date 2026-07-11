"""Tests for GET /approvals/email/{token}."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "exec-1",
        "workflowId": "wf-1",
        "userId": "owner-1",
        "status": "waiting_approval",
        "variables": {"_pending_approval_node": "approval-1"},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    from fastapi import FastAPI

    from src.api.approval_email import router as approval_email_router

    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(approval_email_router)
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row())
    db.workflowexecution.update = AsyncMock(return_value=_execution_row(status="running"))
    db.approval = MagicMock()
    db.approval.create = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_valid_token_records_approval_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    token = create_approval_email_token("exec-1", "approval-1", "approved", "reviewer@example.com")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "approval-result" in resp.headers["location"]
    assert "status=approved" in resp.headers["location"]

    db.approval.create.assert_awaited_once()
    approval_data = db.approval.create.await_args.kwargs["data"]
    assert approval_data["approverUserId"] is None
    assert approval_data["approverEmail"] == "reviewer@example.com"
    assert approval_data["viaEmailLink"] is True
    assert approval_data["decision"] == "approved"

    # The background `executor.resume()` task (mocked-DB, no real checkpointer)
    # inevitably fails and records its own status update; FastAPI's TestClient
    # runs BackgroundTasks synchronously, so >=1 (not ==1) is the correct
    # assertion here — same pattern as
    # test_executions_resume.py::test_resume_marks_execution_running_before_scheduling_task.
    assert db.workflowexecution.update.await_count >= 1
    first_update = db.workflowexecution.update.await_args_list[0]
    assert first_update.kwargs["data"]["status"] == "running"


def test_malformed_token_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _db = _client(monkeypatch)
    resp = client.get("/approvals/email/not-a-real-token", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]


def test_already_resolved_execution_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="completed"))
    token = create_approval_email_token("exec-1", "approval-1", "approved", "reviewer@example.com")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    db.approval.create.assert_not_awaited()


def test_node_mismatch_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards against a stale token from an earlier, already-passed pause
    (e.g. a chained second user-approval node) resolving the wrong gate."""
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(
        return_value=_execution_row(variables={"_pending_approval_node": "approval-2"})
    )
    token = create_approval_email_token("exec-1", "approval-1", "approved", "reviewer@example.com")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    db.approval.create.assert_not_awaited()


def test_execution_not_found_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    token = create_approval_email_token("exec-1", "approval-1", "approved", "reviewer@example.com")
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
