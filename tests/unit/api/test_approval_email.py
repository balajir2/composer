"""Tests for GET /approvals/email/{token}."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# Canonical pending-since timestamp shared by the "matching" fixtures below --
# the token and the stored execution row's variables must carry the same
# value for the happy-path (and other non-mismatch) tests to succeed, since
# the endpoint now binds a token to the specific pause *instance*, not just
# the node_id.
_PENDING_SINCE = "2026-07-11T10:00:00+00:00"


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "exec-1",
        "workflowId": "wf-1",
        "userId": "owner-1",
        "status": "waiting_approval",
        "variables": {
            "_pending_approval_node": "approval-1",
            "_pending_approval_since": _PENDING_SINCE,
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
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
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row())
    db.workflowexecution.update = AsyncMock(return_value=_execution_row(status="running"))
    # The endpoint's status transition is a single atomic `update_many` (not
    # `update`) so the check-then-act race is closed; Prisma Python's
    # update_many returns the affected-row count directly as an int (see
    # src/migration/reconcile.py for the same convention). Default to the
    # "it matched exactly one waiting_approval row" case -- individual tests
    # override this to simulate the guard rejecting a stale/already-resolved
    # row.
    db.workflowexecution.update_many = AsyncMock(return_value=1)
    db.approval = MagicMock()
    db.approval.create = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_valid_token_records_approval_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
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

    # The status transition happens via the atomic update_many, conditioned
    # on the row still being waiting_approval -- this is what closes the
    # double-resolution race a separate find_unique + update pair left open.
    db.workflowexecution.update_many.assert_awaited_once()
    update_many_call = db.workflowexecution.update_many.await_args
    assert update_many_call.kwargs["where"]["id"] == "exec-1"
    assert update_many_call.kwargs["where"]["status"] == "waiting_approval"
    assert update_many_call.kwargs["data"]["status"] == "running"


def test_malformed_token_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _db = _client(monkeypatch)
    resp = client.get("/approvals/email/not-a-real-token", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]


def test_already_resolved_execution_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second click (or a near-simultaneous duplicate request) after the
    row has already flipped away from waiting_approval must not resolve --
    the atomic update_many's WHERE clause won't match, so it updates 0 rows."""
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(return_value=_execution_row(status="completed"))
    db.workflowexecution.update_many = AsyncMock(return_value=0)
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
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
        return_value=_execution_row(
            variables={
                "_pending_approval_node": "approval-2",
                "_pending_approval_since": _PENDING_SINCE,
            }
        )
    )
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    db.approval.create.assert_not_awaited()


def test_stale_pending_since_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a while-loop node pausing twice at the same node_id -- a
    token from the first pause must not resolve the second."""
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(
        return_value=_execution_row(
            variables={
                "_pending_approval_node": "approval-1",
                "_pending_approval_since": "2026-07-11T12:00:00+00:00",
            }
        )
    )
    # Token issued for an EARLIER pause instance at the same node.
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", "2026-07-11T10:00:00+00:00"
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]
    db.approval.create.assert_not_awaited()


def test_execution_not_found_redirects_to_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.jwt import create_approval_email_token

    client, db = _client(monkeypatch)
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", _PENDING_SINCE
    )
    resp = client.get(f"/approvals/email/{token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "status=invalid" in resp.headers["location"]


async def test_approval_email_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Burst of requests from the same client IP eventually 429s, mirroring
    the auth_forgot_password precedent required by the endpoint's own design
    spec (docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md §D)."""
    from src.config import get_settings
    from src.security.rate_limit import RateLimiter, per_minute_config

    client, _db = _client(monkeypatch)

    limiter: RateLimiter = client.app.state.rate_limiter  # type: ignore[attr-defined]
    config = per_minute_config(get_settings().rate_limit_approval_email_per_minute)
    # TestClient's synthetic requests report this as request.client.host.
    ip = "testclient"
    for _ in range(config.capacity):
        await limiter.check("approval_email", ip, config)

    resp = client.get("/approvals/email/not-a-real-token", follow_redirects=False)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
