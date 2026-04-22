"""Tests for workflow list + search endpoints (Phase 7b)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "user-42",
        "name": "Sample",
        "description": "A test workflow",
        "category": "utilities",
        "tags": ["x"],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [],
        "edges": [],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-04-21T00:00:00Z",
        "updatedAt": "2026-04-21T01:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(
    monkeypatch: pytest.MonkeyPatch, rows: list[Any], total: int
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.count = AsyncMock(return_value=total)
    db.workflow.find_many = AsyncMock(return_value=rows)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_list_workflows_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [_wf_row(), _wf_row(id="w2", name="Another")], total=2)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_workflows_filters_by_is_template(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_wf_row(isTemplate=True)], total=1)
    resp = client.get("/workflows?isTemplate=true")
    assert resp.status_code == 200
    assert db.workflow.find_many.await_args.kwargs["where"].get("isTemplate") is True


def test_list_workflows_mine_filters_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_wf_row(userId="dev")], total=1)
    resp = client.get("/workflows?mine=true")
    assert resp.status_code == 200
    # dev-mode fallback (ADR-0015) sets user_id='dev'
    assert db.workflow.find_many.await_args.kwargs["where"].get("userId") == "dev"


def test_list_workflows_limit_over_100_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows?limit=150")
    assert resp.status_code == 422


def test_search_workflows_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_wf_row(name="SearchTest")], total=1)
    resp = client.get("/workflows/search?q=search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "SearchTest"
    where = db.workflow.find_many.await_args.kwargs["where"]
    assert "OR" in where


def test_search_workflows_empty_q_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows/search?q=")
    assert resp.status_code == 422
