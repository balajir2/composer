"""Cloud Scheduler maintenance endpoint wiring and error isolation."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.maintenance.execution_sweeper import SweepResult


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    app.state.db_sessionmaker = db  # the sweeps now take the sessionmaker
    return TestClient(app), db


def _patch_sweeps(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    mocks = {
        "orphaned_running": AsyncMock(return_value=SweepResult(scanned=3, marked_failed=2)),
        "expired_approvals": AsyncMock(return_value=SweepResult(scanned=5, marked_failed=1)),
        "expired_leases": AsyncMock(
            return_value=SweepResult(scanned=4, marked_failed=2, errored=1)
        ),
        "old_execution_events": AsyncMock(return_value=42),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(f"src.api.internal.sweep_{name}", mock)
    return mocks


@pytest.fixture(autouse=True)
def _dev_settings(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    monkeypatch.setenv("ORPHANED_RUNNING_GRACE_SECONDS", "120")
    monkeypatch.setenv("APPROVAL_WAIT_TIMEOUT_HOURS", "0")


def test_sweep_endpoint_skips_disabled_approval_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _patch_sweeps(monkeypatch)
    client, db = _client_with_mock_db()

    response = client.post("/internal/sweep")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "orphaned_running": 2,
        "approvals_expired": 0,
        "leases_expired": 2,
        "events_deleted": 42,
    }
    mocks["orphaned_running"].assert_awaited_once_with(db, grace_seconds=120)
    mocks["expired_leases"].assert_awaited_once_with(db)
    mocks["expired_approvals"].assert_not_awaited()
    mocks["old_execution_events"].assert_awaited_once_with(db, retention_days=30)


def test_sweep_endpoint_runs_positive_approval_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVAL_WAIT_TIMEOUT_HOURS", "168")
    mocks = _patch_sweeps(monkeypatch)
    client, db = _client_with_mock_db()

    response = client.post("/internal/sweep")

    assert response.status_code == 200, response.text
    assert response.json()["approvals_expired"] == 1
    mocks["expired_approvals"].assert_awaited_once_with(db, timeout_hours=168)


@pytest.mark.parametrize(
    "failure", ["orphaned_running", "expired_approvals", "expired_leases", "old_execution_events"]
)
def test_sweep_endpoint_isolates_each_failed_sweep(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("APPROVAL_WAIT_TIMEOUT_HOURS", "168")
    mocks = _patch_sweeps(monkeypatch)
    mocks[failure].side_effect = RuntimeError("transient DB error")
    client, _db = _client_with_mock_db()

    response = client.post("/internal/sweep")

    expected: dict[str, int | None] = {
        "orphaned_running": 2,
        "approvals_expired": 1,
        "leases_expired": 2,
        "events_deleted": 42,
    }
    field = {
        "orphaned_running": "orphaned_running",
        "expired_approvals": "approvals_expired",
        "expired_leases": "leases_expired",
        "old_execution_events": "events_deleted",
    }[failure]
    expected[field] = None
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert all(mock.await_count == 1 for mock in mocks.values())


def test_sweep_endpoint_rejects_unauthenticated_call_without_running_sweeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CLOUD_TASKS_SERVICE_ACCOUNT", "scheduler@test-project.iam.gserviceaccount.com"
    )
    mocks = _patch_sweeps(monkeypatch)
    client, _db = _client_with_mock_db()

    response = client.post("/internal/sweep")

    assert response.status_code == 401
    assert all(mock.await_count == 0 for mock in mocks.values())


def test_sweep_endpoint_accepts_valid_oidc_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CLOUD_TASKS_SERVICE_ACCOUNT", "scheduler@test-project.iam.gserviceaccount.com"
    )
    mocks = _patch_sweeps(monkeypatch)
    client, _db = _client_with_mock_db()

    def _verify(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"email": "scheduler@test-project.iam.gserviceaccount.com"}

    monkeypatch.setattr("src.api.internal.id_token.verify_oauth2_token", _verify)

    response = client.post("/internal/sweep", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 200, response.text
    assert response.json()["leases_expired"] == 2
    mocks["expired_leases"].assert_awaited_once()
