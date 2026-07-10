"""Tests for Jira apiToken encryption-at-rest and redaction-on-read.

A Jira node's apiToken must never round-trip through the API in plaintext
or ciphertext: it is encrypted before being written to the `nodes` JSON
column, and redacted to a fixed marker on every read path.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.engine.workflow import JIRA_TOKEN_REDACTED, decrypt_jira_api_token
from src.main import create_app


def _jira_node(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Jira",
        "domain": "test.atlassian.net",
        "email": "test@example.com",
        "apiToken": "plaintext-secret",
        "instructions": "do a thing",
    }
    data.update(data_overrides)
    return {"id": "j1", "type": "jira", "position": {"x": 0, "y": 0}, "data": data}


def _nodes_with_jira(**data_overrides: Any) -> list[dict[str, Any]]:
    return [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        _jira_node(**data_overrides),
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ]


def _edges() -> list[dict[str, Any]]:
    return [
        {"id": "e1", "source": "s", "target": "j1"},
        {"id": "e2", "source": "j1", "target": "e"},
    ]


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "dev",
        "name": "Sample",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": _nodes_with_jira(),
        "edges": _edges(),
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-04-20T00:00:00Z",
        "updatedAt": "2026-04-20T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    db.workflowassignment = MagicMock()
    db.workflowassignment.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_post_workflow_encrypts_jira_token_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.workflow.create = AsyncMock(return_value=_wf_row())

    resp = client.post(
        "/workflows",
        json={"name": "Sample", "nodes": _nodes_with_jira(), "edges": _edges()},
    )
    assert resp.status_code == 201, resp.text

    # Response never contains the plaintext or any ciphertext.
    resp_jira = next(n for n in resp.json()["nodes"] if n["type"] == "jira")
    assert resp_jira["data"]["apiToken"] == JIRA_TOKEN_REDACTED

    # Persisted row must contain an encrypted value, not the plaintext.
    call_kwargs = db.workflow.create.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data  # prisma.Json wraps the raw value
    persisted_jira = next(n for n in persisted_nodes if n["type"] == "jira")
    stored_token = persisted_jira["data"]["apiToken"]
    assert stored_token != "plaintext-secret"
    assert decrypt_jira_api_token(stored_token) == "plaintext-secret"


def test_get_workflow_redacts_jira_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())

    resp = client.get("/workflows/w1")
    assert resp.status_code == 200, resp.text
    resp_jira = next(n for n in resp.json()["nodes"] if n["type"] == "jira")
    assert resp_jira["data"]["apiToken"] == JIRA_TOKEN_REDACTED
    assert "plaintext-secret" not in resp.text


def test_list_workflows_redacts_jira_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.count = AsyncMock(return_value=1)
    db.workflow.find_many = AsyncMock(return_value=[_wf_row()])

    resp = client.get("/workflows")
    assert resp.status_code == 200, resp.text
    assert "plaintext-secret" not in resp.text


def test_put_workflow_with_redacted_marker_preserves_stored_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the UI echoes back the redacted marker unchanged (user didn't touch
    the token field), the existing encrypted value must be preserved rather
    than being overwritten with the literal marker string."""
    from src.engine.workflow import encrypt_jira_api_token

    stored_encrypted = encrypt_jira_api_token("original-secret")
    existing = _wf_row(nodes=_nodes_with_jira(apiToken=stored_encrypted))

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=existing)

    body = {
        "name": "Sample",
        "nodes": _nodes_with_jira(apiToken=JIRA_TOKEN_REDACTED),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    call_kwargs = db.workflow.update.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted_jira = next(n for n in persisted_nodes if n["type"] == "jira")
    assert persisted_jira["data"]["apiToken"] == stored_encrypted


def test_put_workflow_with_new_plaintext_token_encrypts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.engine.workflow import encrypt_jira_api_token

    stored_encrypted = encrypt_jira_api_token("old-secret")
    existing = _wf_row(nodes=_nodes_with_jira(apiToken=stored_encrypted))

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=existing)

    body = {
        "name": "Sample",
        "nodes": _nodes_with_jira(apiToken="brand-new-secret"),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    call_kwargs = db.workflow.update.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted_jira = next(n for n in persisted_nodes if n["type"] == "jira")
    stored_token = persisted_jira["data"]["apiToken"]
    assert stored_token != "brand-new-secret"
    assert decrypt_jira_api_token(stored_token) == "brand-new-secret"
