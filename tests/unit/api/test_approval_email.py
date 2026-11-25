"""Tests for GET /approvals/email/{token} and POST /approvals/email/{token}/confirm.

P1-1: GET only validates and redirects to the confirmation page — it
never mutates state, so an email security scanner or preview service
prefetching the link is harmless. Only POST /confirm (the frontend
confirmation page's form submission, triggered by a real user click)
performs the actual approve/reject.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.db.models import ApprovalDecision
from src.db.repos import approvals as approvals_repo
from src.db.repos import executions as executions_repo
from src.db.repos.executions import ExecutionRecord, PendingInstance
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session

# Canonical pending-since timestamp shared by the "matching" fixtures below --
# the token and the stored execution row's variables must carry the same
# value for the happy-path (and other non-mismatch) tests to succeed, since
# the endpoint now binds a token to the specific pause *instance*, not just
# the node_id.
_PENDING_SINCE = "2026-07-11T10:00:00+00:00"


def _execution_row(**overrides: Any) -> ExecutionRecord:
    base: dict[str, Any] = {
        "id": "exec-1",
        "workflow_id": "wf-1",
        "user_id": "owner-1",
        "status": "waiting_approval",
        "thread_id": "t1",
        "node_results": {},
        "variables": {
            "_pending_approval_node": "approval-1",
            "_pending_approval_since": _PENDING_SINCE,
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
        "lease_owner": None,
        "lease_expires_at": None,
        "running_since": None,
        "delivery_attempts": 0,
    }
    base.update(overrides)
    return ExecutionRecord(**base)


@dataclass
class _Repos:
    """The patched repo functions and enqueue the routes call."""

    get: AsyncMock
    resume: AsyncMock
    approval_create: AsyncMock
    enqueue: AsyncMock
    events: list[str]


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Repos]:
    from fastapi import FastAPI

    from src.api.approval_email import router as approval_email_router
    from src.security.rate_limit import RateLimiter

    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(approval_email_router)
    events: list[str] = []
    sessionmaker = RecordingSessionmaker(events)

    async def _commit() -> None:
        events.append("db:commit")

    async def _resume(*_a: Any, **_k: Any) -> bool:
        events.append("db:resume")
        return True

    async def _approve(*_a: Any, **_k: Any) -> SimpleNamespace:
        events.append("db:approval")
        return SimpleNamespace(id="approval-row-1")

    async def _enqueue(*_a: Any, **_k: Any) -> None:
        events.append("net:enqueue")

    sessionmaker.session.commit = AsyncMock(side_effect=_commit)
    app.state.db_sessionmaker = sessionmaker
    app.state.rate_limiter = RateLimiter()
    repos = _Repos(
        get=AsyncMock(return_value=_execution_row()),
        resume=AsyncMock(side_effect=_resume),
        approval_create=AsyncMock(side_effect=_approve),
        # POST /confirm enqueues a real Cloud Task (P1-2); patch it so no ADC client is built.
        enqueue=AsyncMock(side_effect=_enqueue),
        events=events,
    )
    monkeypatch.setattr(executions_repo, "get", repos.get)
    monkeypatch.setattr(executions_repo, "resume_if_waiting", repos.resume)
    monkeypatch.setattr(approvals_repo, "create", repos.approval_create)
    monkeypatch.setattr("src.api.approval_email.enqueue_execution", repos.enqueue)
    return TestClient(app), repos


# ---------------------------------------------------------------------------
# GET /approvals/email/{token} — read-only, never mutates
# ---------------------------------------------------------------------------


def test_get_valid_token_redirects_to_confirm_without_mutating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.jwt import create_approval_email_token

    client, repos = _client(monkeypatch)
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "approval-confirm" in resp.headers["location"]
    assert f"token={token}" in resp.headers["location"]
    assert "decision=approved" in resp.headers["location"]

    # The whole point of P1-1: GET must not mutate anything, so a
    # scanner/preview service auto-following this link is harmless.
    repos.approval_create.assert_not_awaited()
    repos.resume.assert_not_awaited()


def test_get_malformed_token_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _repos = _client(monkeypatch)
    resp = client.get("/approvals/email/not-a-real-token", follow_redirects=False)
    assert resp.status_code == 303
    assert "approval-result" in resp.headers["location"]
    assert "status=invalid" in resp.headers["location"]


def test_get_already_resolved_execution_redirects_to_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.jwt import create_approval_email_token

    client, repos = _client(monkeypatch)
    repos.get.return_value = _execution_row(status="completed")
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    repos.approval_create.assert_not_awaited()


def test_get_node_mismatch_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards against a stale token from an earlier, already-passed pause
    (e.g. a chained second user-approval node) resolving the wrong gate."""
    from src.security.jwt import create_approval_email_token

    client, repos = _client(monkeypatch)
    repos.get.return_value = _execution_row(
        variables={
            "_pending_approval_node": "approval-2",
            "_pending_approval_since": _PENDING_SINCE,
        }
    )
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    repos.approval_create.assert_not_awaited()


def test_get_stale_pending_since_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a while-loop node pausing twice at the same node_id -- a
    token from the first pause must not resolve the second."""
    from src.security.jwt import create_approval_email_token

    client, repos = _client(monkeypatch)
    repos.get.return_value = _execution_row(
        variables={
            "_pending_approval_node": "approval-1",
            "_pending_approval_since": "2026-07-11T12:00:00+00:00",
        }
    )
    # Token issued for an EARLIER pause instance at the same node.
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", "2026-07-11T10:00:00+00:00"
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    repos.approval_create.assert_not_awaited()


def test_get_execution_not_found_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, repos = _client(monkeypatch)
    repos.get.return_value = None
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]


async def test_get_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Burst of requests from the same client IP eventually 429s, mirroring
    the auth_forgot_password precedent required by the endpoint's own design
    spec (docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md §D)."""
    from typing import cast

    from src.config import get_settings
    from src.security.rate_limit import RateLimiter, per_minute_config

    client, _repos = _client(monkeypatch)

    limiter = cast("RateLimiter", client.app.state.rate_limiter)  # type: ignore[attr-defined]
    config = per_minute_config(get_settings().rate_limit_approval_email_per_minute)
    # TestClient's synthetic requests report this as request.client.host.
    ip = "testclient"
    for _ in range(config.capacity):
        await limiter.check("approval_email", ip, config)

    resp = client.get("/approvals/email/not-a-real-token", follow_redirects=False)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


# ---------------------------------------------------------------------------
# POST /approvals/email/{token}/confirm — the actual mutation
# ---------------------------------------------------------------------------


def _token(decision: str = "approved", since: str = _PENDING_SINCE) -> str:
    from src.security.jwt import create_approval_email_token

    return create_approval_email_token(
        "exec-1", "approval-1", decision, "reviewer@example.com", since
    )


def test_get_does_not_open_a_write_transaction_or_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)
    resp = client.get(f"/approvals/email/{_token()}", follow_redirects=False)
    assert resp.status_code == 303
    assert repos.events == ["session:enter", "session:exit"]


def test_post_confirm_valid_token_records_approval_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch)
    resp = client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert "approval-result" in resp.headers["location"]
    assert "status=approved" in resp.headers["location"]

    repos.approval_create.assert_awaited_once()
    assert repos.approval_create.await_args.kwargs == {
        "execution_id": "exec-1",
        "node_id": "approval-1",
        "decision": ApprovalDecision.APPROVED,
        "approver_user_id": None,
        "approver_email": "reviewer@example.com",
        "via_email_link": True,
        "note": None,
    }

    repos.resume.assert_awaited_once()
    assert repos.resume.await_args.args[1] == "exec-1"
    kwargs = repos.resume.await_args.kwargs
    assert kwargs["decision"] == "approved"
    assert kwargs["approval_id"] == "approval-row-1"
    # DB2: the guarded statement re-checks the pause instance the token was minted for.
    assert kwargs["expected_pending"] == PendingInstance(node_id="approval-1", since=_PENDING_SINCE)


def test_post_confirm_passes_resume_time_to_the_guarded_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replaces test_post_confirm_clears_stale_lease_in_same_update_many: the lease clear and
    runningSince stamp are the repo's job (phase 4b Task 2); the route must hand it the current
    time. Real-DB proof: tests/integration/test_durable_execution.py."""
    client, repos = _client(monkeypatch)
    frozen_now = datetime(2026, 11, 17, 12, tzinfo=UTC)
    monkeypatch.setattr("src.api.approval_email.utcnow", lambda: frozen_now)
    resp = client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert repos.resume.await_args.kwargs["now"] == frozen_now


def test_post_confirm_race_loses_when_the_guarded_transition_wins_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second confirmation click or a re-pause loses the guarded update. The inserted audit
    row remains uncommitted and the endpoint does not enqueue."""
    client, repos = _client(monkeypatch)
    repos.resume.side_effect = None
    repos.resume.return_value = False
    resp = client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    repos.approval_create.assert_awaited_once()
    assert "db:commit" not in repos.events
    repos.enqueue.assert_not_awaited()


def test_post_confirm_malformed_token_redirects_to_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch)
    resp = client.post("/approvals/email/not-a-real-token/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    repos.resume.assert_not_awaited()
    repos.approval_create.assert_not_awaited()


def test_post_confirm_already_resolved_redirects_to_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-validates from scratch rather than trusting a GET that might
    have preceded it minutes earlier -- state can change in between."""
    client, repos = _client(monkeypatch)
    repos.get.return_value = _execution_row(status="completed")
    resp = client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    repos.resume.assert_not_awaited()
    repos.approval_create.assert_not_awaited()


@pytest.mark.parametrize(
    "variables",
    [
        {"_pending_approval_node": "approval-2", "_pending_approval_since": _PENDING_SINCE},
        {
            "_pending_approval_node": "approval-1",
            "_pending_approval_since": "2026-07-11T12:00:00+00:00",
        },
    ],
)
def test_post_confirm_stale_pause_redirects_to_invalid_without_writing(
    monkeypatch: pytest.MonkeyPatch, variables: dict[str, Any]
) -> None:
    client, repos = _client(monkeypatch)
    repos.get.return_value = _execution_row(variables=variables)
    resp = client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert "status=invalid" in resp.headers["location"]
    repos.resume.assert_not_awaited()
    repos.approval_create.assert_not_awaited()


def test_post_confirm_malformed_decision_claim_is_invalid_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB6: a validly signed token whose decision claim is not a valid decision is `invalid`
    (previously a 500 with the row stuck running). No session is even opened."""
    client, repos = _client(monkeypatch)
    resp = client.post(f"/approvals/email/{_token('maybe')}/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    repos.resume.assert_not_awaited()
    repos.approval_create.assert_not_awaited()
    repos.enqueue.assert_not_awaited()
    assert repos.events == []


def test_post_confirm_enqueues_cloud_task_instead_of_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 full replacement: POST /approvals/email/{token}/confirm must enqueue a Cloud Task
    (kind='resume') rather than scheduling a request-bound BackgroundTask. The decision is
    stamped inside the guarded transition (repo-level), since enqueue_execution's task body has
    no room for it."""
    client, repos = _client(monkeypatch)
    resp = client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert resp.status_code == 303
    repos.enqueue.assert_awaited_once()
    assert repos.enqueue.await_args.args == ("exec-1",)
    assert repos.enqueue.await_args.kwargs["kind"] == "resume"


def test_post_confirm_enqueues_only_after_commit_and_after_the_session_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB1 + short-session rule: guarded transition and approval row in one transaction, commit,
    the session closes, THEN the Cloud Task is enqueued."""
    client, repos = _client(monkeypatch)
    resp = client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert repos.events == [
        "session:enter",
        "db:approval",
        "db:resume",
        "db:commit",
        "session:exit",
        "net:enqueue",
    ]
    assert_no_network_inside_session(repos.events)


def test_post_confirm_rolls_back_and_does_not_enqueue_when_the_approval_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch)
    repos.approval_create.side_effect = RuntimeError("insert failed")
    with pytest.raises(RuntimeError, match="insert failed"):
        client.post(f"/approvals/email/{_token()}/confirm", follow_redirects=False)
    assert "db:commit" not in repos.events
    repos.enqueue.assert_not_awaited()


@pytest.mark.parametrize("method", ["get", "post"])
def test_routes_without_a_database_are_503(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    client, _repos = _client(monkeypatch)
    del client.app.state.db_sessionmaker  # type: ignore[attr-defined]
    url = f"/approvals/email/{_token()}" + ("/confirm" if method == "post" else "")
    resp = getattr(client, method)(url, follow_redirects=False)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "database unavailable"


async def test_post_confirm_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import cast

    from src.config import get_settings
    from src.security.rate_limit import RateLimiter, per_minute_config

    client, _repos = _client(monkeypatch)

    limiter = cast("RateLimiter", client.app.state.rate_limiter)  # type: ignore[attr-defined]
    config = per_minute_config(get_settings().rate_limit_approval_email_per_minute)
    ip = "testclient"
    for _ in range(config.capacity):
        await limiter.check("approval_email", ip, config)

    resp = client.post("/approvals/email/not-a-real-token/confirm", follow_redirects=False)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
