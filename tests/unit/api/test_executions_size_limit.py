"""Tests for POST /executions input size cap (Phase 8)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.engine.events import ExecutionEvent
from src.main import create_app
from src.security.rate_limit import RateLimiter


class _FakeEventStore:
    """In-memory stand-in for PostgresEventStore.append — the background
    task that runs the execution calls event_bus.append(...); a real
    ExecutionEventBus has no such method and NOTIFY needs a live Postgres
    connection, neither of which this unit test has."""

    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    async def append(self, event: ExecutionEvent) -> int:
        seq = len(self.events) + 1
        self.events.append(event)
        return seq


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    app.state.checkpointer = MagicMock()
    app.state.event_bus = _FakeEventStore()
    app.state.rate_limiter = RateLimiter()
    monkeypatch.setattr("src.engine.langgraph_executor.notify_execution_event", AsyncMock())
    # P1-2: POST /executions now enqueues a real Cloud Task instead of a
    # BackgroundTask — patch it to a no-op so this test doesn't try to
    # construct a real `tasks_v2.CloudTasksAsyncClient()` (which resolves
    # ADC credentials this test environment doesn't have).
    monkeypatch.setattr("src.api.executions.enqueue_execution", AsyncMock())
    return TestClient(app)


def test_execution_input_over_size_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Input > 1 MB → 413."""
    client = _client(monkeypatch)
    huge_input = "x" * 1_500_000
    resp = client.post("/executions", json={"workflowId": "w1", "input": huge_input})
    assert resp.status_code == 413
    assert "max_bytes" in resp.json()["detail"]


def test_execution_input_non_ascii_measured_by_utf8_bytes_not_escaped_json_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The size check must measure the payload's true UTF-8 byte size, not
    the character length of json.dumps()'s default ensure_ascii=True output.
    That default escapes every non-ASCII character to a `\\uXXXX` sequence
    (6 chars for a 3-byte UTF-8 character like '€'), so a 300,000-char
    string of euro signs is only 900,002 real UTF-8 bytes — comfortably
    under the 1 MB cap — but its escaped-JSON character count is 1,800,002,
    which the old `len(json.dumps(...))` check would wrongly reject as
    oversized (P1-6)."""
    client = _client(monkeypatch)
    non_ascii_input = "€" * 300_000
    resp = client.post("/executions", json={"workflowId": "w1", "input": non_ascii_input})
    assert resp.status_code != 413


def test_execution_input_small_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small input is accepted (passes size check)."""
    client = _client(monkeypatch)
    resp = client.post("/executions", json={"workflowId": "w1", "input": "small"})
    # 202 on happy path; any non-413 proves size check passed
    assert resp.status_code != 413
