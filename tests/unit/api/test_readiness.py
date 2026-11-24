"""Database-aware readiness endpoint behavior."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.db.health import DatabaseHealth
from src.main import create_app


@pytest.fixture
def ready_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, AsyncMock, FastAPI]:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    app = create_app()
    app.state.db_engine = MagicMock()
    app.state.runtime_initialized = True
    app.state.draining = False
    check = AsyncMock(return_value=DatabaseHealth(reachable=True, schema_current=True, error=None))
    monkeypatch.setattr("src.api.readiness.check_database", check)
    monkeypatch.setattr("src.api.readiness.alembic_head_revision", lambda: "head-rev")
    monkeypatch.setattr(
        "src.api.readiness.get_settings", lambda: app.state.telemetry_runtime.settings
    )
    return TestClient(app), check, app


def test_ready_returns_bounded_component_states_and_ignores_telemetry_degradation(
    ready_client: tuple[TestClient, AsyncMock, FastAPI],
) -> None:
    client, check, app = ready_client
    app.state.telemetry_runtime.settings.telemetry_enabled = True

    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ready",
        "components": {
            "database": "ready",
            "schema": "current",
            "runtime": "ready",
            "telemetry": "degraded",
        },
    }
    check.assert_awaited_once_with(
        app.state.db_engine, timeout_seconds=1.0, expected_revision="head-rev"
    )
    assert "database_url" not in response.text


@pytest.mark.parametrize(
    ("health", "expected_schema"),
    [
        (DatabaseHealth(reachable=False, schema_current=None, error="TimeoutError"), "unknown"),
        (DatabaseHealth(reachable=True, schema_current=False, error=None), "mismatch"),
    ],
)
def test_database_failure_returns_non_200_without_exception_details(
    ready_client: tuple[TestClient, AsyncMock, FastAPI],
    health: DatabaseHealth,
    expected_schema: str,
) -> None:
    client, check, _ = ready_client
    check.return_value = health

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["components"]["database"] == (
        "unavailable" if not health.reachable else "ready"
    )
    assert response.json()["components"]["schema"] == expected_schema
    assert "TimeoutError" not in response.text
    assert "localhost" not in response.text


def test_draining_and_missing_runtime_initialization_return_non_200(
    ready_client: tuple[TestClient, AsyncMock, FastAPI],
) -> None:
    client, _, app = ready_client
    app.state.draining = True
    assert client.get("/ready").status_code == 503

    app.state.draining = False
    app.state.runtime_initialized = False
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["components"]["runtime"] == "initializing"


def test_missing_database_engine_returns_non_200(
    ready_client: tuple[TestClient, AsyncMock, FastAPI],
) -> None:
    client, check, app = ready_client
    del app.state.db_engine

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["components"]["database"] == "unavailable"
    check.assert_not_awaited()


def test_cold_start_first_attempt_unreachable_then_ready_returns_200(
    ready_client: tuple[TestClient, AsyncMock, FastAPI],
) -> None:
    client, check, _ = ready_client
    check.side_effect = [
        DatabaseHealth(reachable=False, schema_current=None, error="TimeoutError"),
        DatabaseHealth(reachable=True, schema_current=True, error=None),
    ]

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["components"]["database"] == "ready"
    assert check.await_count == 2
    timeouts = [call.kwargs["timeout_seconds"] for call in check.await_args_list]
    assert timeouts[0] == 1.0
    # Both attempts plus the backoff must fit Cloud Run's 2 s startup-probe timeout.
    assert sum(timeouts) + 0.1 < 2.0


def test_both_attempts_unreachable_returns_503_within_budget_without_details(
    ready_client: tuple[TestClient, AsyncMock, FastAPI],
) -> None:
    client, check, _ = ready_client
    check.return_value = DatabaseHealth(reachable=False, schema_current=None, error="TimeoutError")

    started = time.monotonic()
    response = client.get("/ready")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["components"]["database"] == "unavailable"
    assert check.await_count == 2
    assert elapsed < 1.0  # mocked checks return instantly; only the backoff sleeps
    assert "TimeoutError" not in response.text


def test_check_raising_on_both_attempts_returns_503_without_exception_text(
    ready_client: tuple[TestClient, AsyncMock, FastAPI],
) -> None:
    client, check, _ = ready_client
    check.side_effect = RuntimeError("password=hunter2 host=db.internal")

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["components"]["database"] == "unavailable"
    assert "hunter2" not in response.text
    assert "db.internal" not in response.text
