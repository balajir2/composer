"""Tests for POST /internal/sweep (P1-2/P1-4).

Cloud Scheduler hits this endpoint on a fixed interval to run all four
sweep functions once per invocation, replacing the in-process
`_sweeper_loop` background task for lease recovery + event retention
cleanup in production (2026-07-14 revision: `--no-cpu-throttling` was
removed, so nothing may rely on in-process ticking between requests).
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.executionevent = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    return TestClient(app), db


def test_sweep_endpoint_runs_all_four_sweeps_and_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")  # dev mode: OIDC check skipped
    client, _db = _client_with_mock_db()

    from src.maintenance.execution_sweeper import SweepResult

    calls: list[str] = []

    async def _fake_sweep_stuck_executions(db: object, *, stuck_after_seconds: int) -> SweepResult:
        calls.append("stuck")
        assert stuck_after_seconds == 900
        return SweepResult(scanned=3, marked_failed=2)

    async def _fake_sweep_expired_approvals(db: object, *, timeout_hours: int) -> SweepResult:
        calls.append("approvals")
        assert timeout_hours == 168
        return SweepResult(scanned=5, marked_failed=1)

    async def _fake_sweep_expired_leases(db: object, *, max_delivery_attempts: int) -> SweepResult:
        calls.append("leases")
        assert max_delivery_attempts == 5
        return SweepResult(scanned=4, marked_failed=1)

    async def _fake_sweep_old_execution_events(db: object, *, retention_days: int) -> int:
        calls.append("events")
        assert retention_days == 30
        return 42

    monkeypatch.setattr("src.api.internal.sweep_stuck_executions", _fake_sweep_stuck_executions)
    monkeypatch.setattr("src.api.internal.sweep_expired_approvals", _fake_sweep_expired_approvals)
    monkeypatch.setattr("src.api.internal.sweep_expired_leases", _fake_sweep_expired_leases)
    monkeypatch.setattr(
        "src.api.internal.sweep_old_execution_events", _fake_sweep_old_execution_events
    )

    resp = client.post("/internal/sweep")

    assert resp.status_code == 200, resp.text
    assert calls == ["stuck", "approvals", "leases", "events"]
    assert resp.json() == {
        "stuck": 2,
        "approvals_expired": 1,
        # leases_recovered = scanned(4) - marked_failed(1) (dead-lettered);
        # the ones NOT dead-lettered were recovered (re-enqueued).
        "leases_recovered": 3,
        "events_deleted": 42,
    }


def test_sweep_endpoint_rejects_unauthenticated_call_without_running_sweeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once CLOUD_TASKS_SERVICE_ACCOUNT is configured (production), a call
    with no Authorization header must be rejected — and critically, must
    not run any of the four sweeps first."""
    monkeypatch.setenv(
        "CLOUD_TASKS_SERVICE_ACCOUNT", "scheduler@test-project.iam.gserviceaccount.com"
    )
    client, _db = _client_with_mock_db()

    calls: list[str] = []

    async def _fake_sweep_stuck_executions(db: object, *, stuck_after_seconds: int) -> object:
        calls.append("stuck")
        raise AssertionError("should not be called")

    async def _fake_sweep_expired_approvals(db: object, *, timeout_hours: int) -> object:
        calls.append("approvals")
        raise AssertionError("should not be called")

    async def _fake_sweep_expired_leases(db: object, *, max_delivery_attempts: int) -> object:
        calls.append("leases")
        raise AssertionError("should not be called")

    async def _fake_sweep_old_execution_events(db: object, *, retention_days: int) -> object:
        calls.append("events")
        raise AssertionError("should not be called")

    monkeypatch.setattr("src.api.internal.sweep_stuck_executions", _fake_sweep_stuck_executions)
    monkeypatch.setattr("src.api.internal.sweep_expired_approvals", _fake_sweep_expired_approvals)
    monkeypatch.setattr("src.api.internal.sweep_expired_leases", _fake_sweep_expired_leases)
    monkeypatch.setattr(
        "src.api.internal.sweep_old_execution_events", _fake_sweep_old_execution_events
    )

    resp = client.post("/internal/sweep")

    assert resp.status_code == 401
    assert calls == []


def test_sweep_endpoint_accepts_valid_oidc_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: the same OIDC verification path Task 10 built for
    claim-and-run also accepts a valid Cloud Scheduler token here — proves
    the helper was reused, not duplicated with different behavior."""
    monkeypatch.setenv(
        "CLOUD_TASKS_SERVICE_ACCOUNT", "scheduler@test-project.iam.gserviceaccount.com"
    )
    client, _db = _client_with_mock_db()

    from src.maintenance.execution_sweeper import SweepResult

    async def _fake_sweep_stuck(db: object, *, stuck_after_seconds: int) -> SweepResult:
        return SweepResult(scanned=0, marked_failed=0)

    async def _fake_sweep_approvals(db: object, *, timeout_hours: int) -> SweepResult:
        return SweepResult(scanned=0, marked_failed=0)

    async def _fake_sweep_leases(db: object, *, max_delivery_attempts: int) -> SweepResult:
        return SweepResult(scanned=0, marked_failed=0)

    async def _fake_sweep_events(db: object, *, retention_days: int) -> int:
        return 0

    monkeypatch.setattr("src.api.internal.sweep_stuck_executions", _fake_sweep_stuck)
    monkeypatch.setattr("src.api.internal.sweep_expired_approvals", _fake_sweep_approvals)
    monkeypatch.setattr("src.api.internal.sweep_expired_leases", _fake_sweep_leases)
    monkeypatch.setattr("src.api.internal.sweep_old_execution_events", _fake_sweep_events)

    def _verify(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"email": "scheduler@test-project.iam.gserviceaccount.com"}

    monkeypatch.setattr("src.api.internal.id_token.verify_oauth2_token", _verify)

    resp = client.post("/internal/sweep", headers={"Authorization": "Bearer some-valid-token"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "stuck": 0,
        "approvals_expired": 0,
        "leases_recovered": 0,
        "events_deleted": 0,
    }
