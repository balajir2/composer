"""Tests for POST /workflows."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from src.main import create_app


def _workflow_row(overrides: dict[str, Any] | None = None) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "wf1",
        "userId": None,
        "name": "Smoke",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-04-20T00:00:00Z",
        "updatedAt": "2026-04-20T00:00:00Z",
    }
    if overrides:
        base.update(overrides)
    return SimpleNamespace(**base)


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.create = AsyncMock(return_value=_workflow_row())
    # Override the dependency by setting app.state.db directly for the test.
    app.state.db = db
    app.state.checkpointer = MagicMock()
    return TestClient(app), db


def test_post_workflow_creates_and_returns_row() -> None:
    client, db = _client_with_mock_db()
    payload = {
        "name": "Smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    resp = client.post("/workflows", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "wf1"
    assert body["name"] == "Smoke"
    db.workflow.create.assert_awaited_once()


def test_post_workflow_rejects_invalid_shape() -> None:
    client, _ = _client_with_mock_db()
    # Missing end node
    payload = {
        "name": "Bad",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        ],
        "edges": [],
    }
    resp = client.post("/workflows", json=payload)
    assert resp.status_code == 422
    assert "at least one end node" in resp.json()["detail"]


def test_post_workflow_rejects_unknown_node_type() -> None:
    client, _ = _client_with_mock_db()
    payload = {
        "name": "Bad",
        "nodes": [
            {"id": "s", "type": "nope", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        ],
        "edges": [],
    }
    resp = client.post("/workflows", json=payload)
    assert resp.status_code == 422  # Pydantic validation, standard FastAPI shape
