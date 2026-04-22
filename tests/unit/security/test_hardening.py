"""Security regression tests (Phase 8).

Invariants from spec §6 not already covered by per-feature tests:
- Special-char workflow names round-trip (no SQL injection / XSS stripping)
- Path-traversal in workflow_id → 404
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _wf_row_from_create(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="w-new",
        userId="dev",
        name=name,
        description=None,
        category=None,
        tags=[],
        difficulty=None,
        estimatedTime=None,
        nodes=[],
        edges=[],
        version=None,
        isTemplate=False,
        isPublic=False,
        createdAt="2026-04-22T00:00:00Z",
        updatedAt="2026-04-22T00:00:00Z",
    )


def _client_with_create(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()

    async def _create(*, data: dict[str, Any]) -> Any:
        return _wf_row_from_create(data["name"])

    db.workflow.create = _create
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def _client_with_find_none(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


_MINIMAL: dict[str, Any] = {
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


@pytest.mark.parametrize(
    "name",
    [
        "Normal Name",
        "Unicode 日本語 テスト",
        "Emoji 🎉 🔥 💯",
        "Quotes \"'`",
        "HTML <script>alert('x')</script>",
        "SQL'; DROP TABLE workflows; --",
    ],
)
def test_workflow_name_round_trips_special_chars(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Special chars in workflow names pass through create without transformation."""
    client, _ = _client_with_create(monkeypatch)
    resp = client.post("/workflows", json={"name": name, **_MINIMAL})
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == name


def test_workflow_id_path_traversal_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path-traversal IDs are opaque strings; workflow lookup returns 404."""
    client = _client_with_find_none(monkeypatch)
    for bad_id in ["..%2F..%2Fetc%2Fpasswd", "%00", "%2e%2e%2fetc"]:
        resp = client.get(f"/workflows/{bad_id}")
        assert resp.status_code == 404, f"id={bad_id!r} got {resp.status_code}"


def test_execution_id_path_traversal_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path-traversal IDs on execution lookup return 404."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
    client = TestClient(app)

    for bad_id in ["..%2F..%2Fetc%2Fpasswd", "%00"]:
        resp = client.get(f"/executions/{bad_id}")
        assert resp.status_code == 404, f"id={bad_id!r} got {resp.status_code}"
