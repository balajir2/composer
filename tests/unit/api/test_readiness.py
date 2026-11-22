"""Database-aware readiness endpoint behavior."""

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
