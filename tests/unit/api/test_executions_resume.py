"""Tests for POST /executions/{id}/resume (Phase 5a)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.models import ApprovalDecision
from src.db.repos import approvals as approvals_repo
from src.db.repos import executions as executions_repo
from src.db.repos import users as users_repo
from src.db.repos.executions import ExecutionRecord
from src.engine.events import ExecutionEvent
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import (
    RecordingSessionmaker,
    assert_no_network_inside_session,
    make_user,
)


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


def _execution_row(**overrides: Any) -> ExecutionRecord:
    base: dict[str, Any] = {
        "id": "e1",
        "workflow_id": "w1",
        "user_id": "dev",
        "status": "waiting_approval",
        "thread_id": "t1",
        "node_results": {},
        "variables": {
            "_pending_approval_node": "ua",
            "_pending_approval_prompt": "Approve?",
        },
        "input": None,
        "output": None,
        "error": None,
        "failure_code": None,
        "current_node_id": None,
        "started_at": datetime(2026, 4, 21, tzinfo=UTC),
        "completed_at": None,
        "original_owner_email": None,
        "idempotency_key": None,
        "lease_owner": "stale-worker",
        "lease_expires_at": None,
        "running_since": None,
        "delivery_attempts": 0,
    }
    base.update(overrides)
    return ExecutionRecord(**base)


@dataclass
class _Repos:
    """The patched repo functions the resume route calls."""

    get: AsyncMock
    resume: AsyncMock
    approval_create: AsyncMock


def _client_with_execution(
    monkeypatch: pytest.MonkeyPatch,
    execution: ExecutionRecord | None,
    *,
    events: list[str] | None = None,
) -> tuple[TestClient, _Repos]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.checkpointer = MagicMock()
    app.state.event_bus = _FakeEventStore()
    app.state.rate_limiter = RateLimiter()
    sessionmaker = RecordingSessionmaker(events if events is not None else [])
    app.state.db_sessionmaker = sessionmaker
    monkeypatch.setattr("src.engine.langgraph_executor.notify_execution_event", AsyncMock())

    log = events if events is not None else []

    async def _resume(*_a: Any, **_k: Any) -> bool:
        log.append("db:resume")
        return True

    async def _approve(*_a: Any, **_k: Any) -> None:
        log.append("db:approval")

    async def _commit() -> None:
        log.append("db:commit")

    sessionmaker.session.commit = AsyncMock(side_effect=_commit)
    repos = _Repos(
        get=AsyncMock(return_value=execution),
        resume=AsyncMock(side_effect=_resume),
        approval_create=AsyncMock(side_effect=_approve),
    )
    monkeypatch.setattr(executions_repo, "get", repos.get)
    monkeypatch.setattr(executions_repo, "resume_if_waiting", repos.resume)
    monkeypatch.setattr(approvals_repo, "create", repos.approval_create)
    # Dev-mode user 'dev' resolves to a member (the live user check runs with a sessionmaker).
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="dev")))
    return TestClient(app), repos


def test_resume_approved_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "approved", "note": "lgtm"},
    )
    assert resp.status_code == 200, resp.text
    repos.approval_create.assert_awaited_once()
    assert repos.approval_create.await_args.kwargs == {
        "execution_id": "e1",
        "node_id": "ua",
        "decision": ApprovalDecision.APPROVED,
        "approver_user_id": "dev",  # dev-mode fallback
        "approver_email": None,
        "via_email_link": False,
        "note": "lgtm",
    }


def test_resume_rejected_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post(
        "/executions/e1/resume",
        json={"decision": "rejected"},
    )
    assert resp.status_code == 200
    kwargs = repos.approval_create.await_args.kwargs
    assert kwargs["decision"] == ApprovalDecision.REJECTED
    assert kwargs["note"] is None


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
    """The endpoint flips status via the guarded transition BEFORE enqueueing, and returns a
    response that reflects the transition (not the stale pre-transition row)."""
    client, repos = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    repos.resume.assert_awaited_once()
    assert repos.resume.await_args.args[1] == "e1"
    assert repos.resume.await_args.kwargs["decision"] == "approved"
    assert "expected_pending" not in repos.resume.await_args.kwargs  # in-app resume: status-only
    assert resp.json()["status"] == "running"


def test_resume_passes_resume_time_to_the_guarded_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replaces test_resume_clears_stale_lease_in_same_update_many: the lease clear and
    runningSince stamp are the repo's job (tests/integration/db/repos, phase 4b Task 2); the route
    must hand it the current time. Real-DB proof of the lease clear:
    tests/integration/test_durable_execution.py."""
    client, repos = _client_with_execution(monkeypatch, _execution_row())
    frozen_now = datetime(2026, 11, 17, 12, tzinfo=UTC)
    monkeypatch.setattr("src.api.executions.utcnow", lambda: frozen_now)
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    assert repos.resume.await_args.kwargs["now"] == frozen_now


def test_resume_race_loses_when_the_guarded_transition_wins_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-1: the atomic waiting_approval -> running transition is the real concurrency guard. If a
    concurrent request already flipped the row, this request must not create a second audit row
    or enqueue a second resumption: 409, no side effects."""
    events: list[str] = []
    client, repos = _client_with_execution(monkeypatch, _execution_row(), events=events)
    repos.resume.side_effect = None
    repos.resume.return_value = False
    enqueue = AsyncMock()
    monkeypatch.setattr("src.api.executions.enqueue_execution", enqueue)
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 409
    repos.approval_create.assert_not_awaited()
    enqueue.assert_not_awaited()


def test_resume_without_a_database_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_execution(monkeypatch, _execution_row())
    del client.app.state.db_sessionmaker  # type: ignore[attr-defined]
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "database unavailable"


def test_resume_enqueues_only_after_commit_and_after_the_session_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB1 + short-session rule: one transaction (guarded transition, approval row, commit), the
    session closes, THEN the Cloud Task is enqueued."""
    events: list[str] = []
    client, _repos = _client_with_execution(monkeypatch, _execution_row(), events=events)

    async def _enqueue(execution_id: str, *, kind: str) -> None:
        events.append("net:enqueue")

    monkeypatch.setattr("src.api.executions.enqueue_execution", _enqueue)
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    # The auth dependency's live user check opens its own short session first.
    assert events[-6:] == [
        "session:enter",
        "db:resume",
        "db:approval",
        "db:commit",
        "session:exit",
        "net:enqueue",
    ]
    assert_no_network_inside_session(events)


def test_resume_rolls_back_and_does_not_enqueue_when_the_approval_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The approval insert raising leaves the transition uncommitted (the session exits without
    commit, so it rolls back) and nothing is enqueued."""
    events: list[str] = []
    client, repos = _client_with_execution(monkeypatch, _execution_row(), events=events)
    repos.approval_create.side_effect = RuntimeError("insert failed")
    enqueue = AsyncMock()
    monkeypatch.setattr("src.api.executions.enqueue_execution", enqueue)
    with pytest.raises(RuntimeError, match="insert failed"):
        client.post("/executions/e1/resume", json={"decision": "approved"})
    assert "db:commit" not in events
    enqueue.assert_not_awaited()


def test_get_execution_non_owner_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner reading an execution gets 404 (info-leak tight)."""
    client, repos = _client_with_execution(monkeypatch, _execution_row(user_id="someone-else"))
    resp = client.get("/executions/e1")
    assert resp.status_code == 404
    repos.resume.assert_not_awaited()


def test_resume_non_owner_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-owner resuming an execution gets 404 (info-leak tight)."""
    client, repos = _client_with_execution(
        monkeypatch, _execution_row(user_id="someone-else", status="waiting_approval")
    )
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 404
    repos.resume.assert_not_awaited()


# ---------------------------------------------------------------------------
# Phase 9 Task 5 — Admin role bypass on resume
# ---------------------------------------------------------------------------


def test_admin_can_resume_other_users_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin can resume an execution belonging to another user."""
    from src.security.auth import get_current_role

    client, repos = _client_with_execution(monkeypatch, _execution_row(user_id="someone-else"))
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    repos.approval_create.assert_awaited_once()
    assert repos.approval_create.await_args.kwargs["approver_user_id"] == "dev"


def test_resume_enqueues_cloud_task_instead_of_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 full replacement: POST /executions/{id}/resume must enqueue a Cloud Task
    (kind='resume') rather than scheduling a request-bound BackgroundTask. The decision is
    stamped inside the guarded transition (repo-level, covered by the integration oracle)
    because enqueue_execution's task body has no room for it."""
    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)

    client, repos = _client_with_execution(monkeypatch, _execution_row())
    resp = client.post("/executions/e1/resume", json={"decision": "approved"})
    assert resp.status_code == 200, resp.text
    assert enqueued == [("e1", "resume")]
    assert repos.resume.await_args.kwargs["decision"] == "approved"


def test_resume_does_not_emit_approval_resumed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /resume no longer emits approval-resumed (dropped in DES-007 Phase 9a).

    DES-007 covers the post-resume activity via the subsequent node_started event
    emitted by the LangGraphExecutor after resume() drives the graph.
    """
    client, _repos = _client_with_execution(monkeypatch, _execution_row())
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
