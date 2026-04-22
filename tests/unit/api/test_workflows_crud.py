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
    # With authz, where is {"AND": [authz_or, {"isTemplate": True}]}
    where = db.workflow.find_many.await_args.kwargs["where"]
    and_clauses = where.get("AND", [])
    assert any(c.get("isTemplate") is True for c in and_clauses)


def test_list_workflows_mine_filters_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_wf_row(userId="dev")], total=1)
    resp = client.get("/workflows?mine=true")
    assert resp.status_code == 200
    # mine=true → authz_where = {"userId": user_id} (no OR wrapper)
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
    # With authz: where = {"AND": [authz_or, text_match_or]}
    assert "AND" in where
    where_str = str(where)
    assert "OR" in where_str


def test_search_workflows_empty_q_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows/search?q=")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 2 — GET /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def _client_fetch(monkeypatch: pytest.MonkeyPatch, row: Any | None) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=row)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_get_workflow_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _wf_row(id="wx", name="Hello", userId="dev")  # match dev-mode caller
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Hello"


def test_get_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_fetch(monkeypatch, None)
    resp = client.get("/workflows/ghost")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 3 — PUT /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def _client_put(
    monkeypatch: pytest.MonkeyPatch,
    existing: Any | None,
    updated: Any | None,
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=updated)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


_VALID_BODY: dict[str, Any] = {
    "name": "Updated",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


def test_put_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="dev")  # dev-mode fallback user
    updated = _wf_row(id="w1", userId="dev", name="Updated")
    client, _ = _client_put(monkeypatch, existing, updated)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Updated"


def test_put_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="someone-else")
    client, _ = _client_put(monkeypatch, existing, None)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 403


def test_put_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_put(monkeypatch, None, None)
    resp = client.put("/workflows/ghost", json=_VALID_BODY)
    assert resp.status_code == 404


def test_put_workflow_invalid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """No start node → validator raises → 422."""
    existing = _wf_row(id="w1", userId="dev")
    client, _ = _client_put(monkeypatch, existing, None)
    bad_body: dict[str, Any] = {
        "name": "Bad",
        "nodes": [
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [],
    }
    resp = client.put("/workflows/w1", json=bad_body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 4 — DELETE /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def _client_delete(
    monkeypatch: pytest.MonkeyPatch, existing: Any | None
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.delete = AsyncMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_delete_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="dev")
    client, db = _client_delete(monkeypatch, existing)
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 204
    db.workflow.delete.assert_awaited_once()


def test_delete_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="other")
    client, _ = _client_delete(monkeypatch, existing)
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 403


def test_delete_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_delete(monkeypatch, None)
    resp = client.delete("/workflows/ghost")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 8 — workflow read authz (ADR-0021)
# ---------------------------------------------------------------------------


def test_get_workflow_private_not_owner_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner reading a private workflow gets 404 (info-leak tight)."""
    row = _wf_row(id="wx", userId="someone-else", isPublic=False)
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 404


def test_get_workflow_public_non_owner_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public workflows are readable by non-owners."""
    row = _wf_row(id="wx", userId="other", isPublic=True, name="Public")
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Public"


def test_list_workflows_where_clause_has_authz_or(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List query must OR(isPublic=true, userId=caller)."""
    client, db = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    where = db.workflow.find_many.await_args.kwargs["where"]
    # Inspect: the authz OR should be nested somewhere
    where_str = str(where)
    assert "isPublic" in where_str
    assert "OR" in where_str
