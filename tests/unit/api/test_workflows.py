"""Tests for POST /workflows."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos.workflows import WorkflowRecord
from src.main import create_app
from tests.unit.db_fakes import (
    WorkflowRepoMocks,
    make_workflow,
    override_session,
    patch_workflow_repos,
)


def _workflow_row(overrides: dict[str, Any] | None = None) -> WorkflowRecord:
    base: dict[str, Any] = {
        "id": "wf1",
        "user_id": None,
        "name": "Smoke",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    if overrides:
        base.update(overrides)
    return make_workflow(**base)


def _client_with_mock_db(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, WorkflowRepoMocks]:
    app = create_app()
    app.state.db = MagicMock()
    app.state.checkpointer = MagicMock()
    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.create.return_value = _workflow_row()
    return TestClient(app), repos


def test_post_workflow_creates_and_returns_row(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client_with_mock_db(monkeypatch)
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
    repos.create.assert_awaited_once()


def test_post_workflow_rejects_invalid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_mock_db(monkeypatch)
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


def test_post_workflow_rejects_unknown_node_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_with_mock_db(monkeypatch)
    payload = {
        "name": "Bad",
        "nodes": [
            {"id": "s", "type": "nope", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        ],
        "edges": [],
    }
    resp = client.post("/workflows", json=payload)
    assert resp.status_code == 422  # Pydantic validation, standard FastAPI shape
