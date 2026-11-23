"""Tests for confluence node apiToken encryption-at-rest and redaction-on-read.

Mirrors tests/unit/api/test_workflows_vector_db_keys.py's pattern exactly.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos.workflows import WorkflowRecord
from src.main import create_app
from src.security.encryption import REDACTED_MARKER, decrypt_marked
from tests.unit.db_fakes import (
    WorkflowRepoMocks,
    make_workflow,
    override_session,
    patch_workflow_repos,
)


def _confluence_node(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Confluence",
        "domain": "test.atlassian.net",
        "email": "test@example.com",
        "apiToken": "plaintext-confluence-token",
        "operation": "create_or_update_page",
        "spaceKey": "MB",
        "title": "Weekly Report",
    }
    data.update(data_overrides)
    return {"id": "c1", "type": "confluence", "position": {"x": 0, "y": 0}, "data": data}


def _nodes_with_confluence(**data_overrides: Any) -> list[dict[str, Any]]:
    return [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        _confluence_node(**data_overrides),
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ]


def _edges() -> list[dict[str, Any]]:
    return [
        {"id": "e1", "source": "s", "target": "c1"},
        {"id": "e2", "source": "c1", "target": "e"},
    ]


def _wf_row(**overrides: Any) -> WorkflowRecord:
    base: dict[str, Any] = {
        "id": "w1",
        "user_id": "dev",
        "name": "Sample",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimated_time": None,
        "nodes": _nodes_with_confluence(),
        "edges": _edges(),
        "version": None,
        "is_template": False,
        "is_public": False,
        "created_at": "2026-07-19T00:00:00Z",
        "updated_at": "2026-07-19T00:00:00Z",
    }
    base.update(overrides)
    return make_workflow(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, WorkflowRepoMocks]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), repos


def test_post_workflow_encrypts_confluence_token_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch)
    repos.create.return_value = _wf_row()

    resp = client.post(
        "/workflows",
        json={"name": "Sample", "nodes": _nodes_with_confluence(), "edges": _edges()},
    )
    assert resp.status_code == 201, resp.text

    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "confluence")
    assert resp_node["data"]["apiToken"] == REDACTED_MARKER

    persisted_nodes = repos.create.await_args.args[1]["nodes"]
    persisted = next(n for n in persisted_nodes if n["type"] == "confluence")
    stored_token = persisted["data"]["apiToken"]
    assert stored_token != "plaintext-confluence-token"
    assert decrypt_marked(stored_token) == "plaintext-confluence-token"


def test_get_workflow_redacts_confluence_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)
    repos.get.return_value = _wf_row()

    resp = client.get("/workflows/w1")
    assert resp.status_code == 200, resp.text
    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "confluence")
    assert resp_node["data"]["apiToken"] == REDACTED_MARKER
    assert "plaintext-confluence-token" not in resp.text


def test_list_workflows_redacts_confluence_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)
    repos.list_page.return_value = ([_wf_row()], 1)

    resp = client.get("/workflows")
    assert resp.status_code == 200, resp.text
    assert "plaintext-confluence-token" not in resp.text


def test_put_workflow_with_redacted_marker_preserves_stored_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.encryption import encrypt_marked

    stored_token = encrypt_marked("original-confluence-token")
    existing = _wf_row(nodes=_nodes_with_confluence(apiToken=stored_token))

    client, repos = _client(monkeypatch)
    repos.get.return_value = existing
    repos.update.return_value = existing

    body = {
        "name": "Sample",
        "nodes": _nodes_with_confluence(apiToken=REDACTED_MARKER),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    persisted_nodes = repos.update.await_args.args[2]["nodes"]
    persisted = next(n for n in persisted_nodes if n["type"] == "confluence")
    assert persisted["data"]["apiToken"] == stored_token


def test_put_workflow_with_new_plaintext_token_encrypts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.encryption import encrypt_marked

    existing = _wf_row(nodes=_nodes_with_confluence(apiToken=encrypt_marked("old-token")))

    client, repos = _client(monkeypatch)
    repos.get.return_value = existing
    repos.update.return_value = existing

    body = {
        "name": "Sample",
        "nodes": _nodes_with_confluence(apiToken="brand-new-token"),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    persisted_nodes = repos.update.await_args.args[2]["nodes"]
    persisted = next(n for n in persisted_nodes if n["type"] == "confluence")
    assert decrypt_marked(persisted["data"]["apiToken"]) == "brand-new-token"
