"""Tests for HTTP node httpHeaders encryption-at-rest and redaction-on-read
for secret-looking header values (P0-5).

Mirrors tests/unit/api/test_workflows_jira_tokens.py's pattern — same
class of gap, applied to the http node's headers dict. Only header
NAMES matching SENSITIVE_HEADER_NAMES (Authorization, X-Api-Key, ...)
get their VALUES encrypted/redacted; other headers pass through as-is.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.encryption import REDACTED_MARKER, decrypt_marked


def _http_node(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "HTTP",
        "httpUrl": "https://api.example.com/things",
        "httpMethod": "GET",
        "httpHeaders": {
            "Authorization": "Bearer plaintext-secret-token",
            "Content-Type": "application/json",
        },
    }
    data.update(data_overrides)
    return {"id": "h1", "type": "http", "position": {"x": 0, "y": 0}, "data": data}


def _nodes_with_http(**data_overrides: Any) -> list[dict[str, Any]]:
    return [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        _http_node(**data_overrides),
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ]


def _edges() -> list[dict[str, Any]]:
    return [
        {"id": "e1", "source": "s", "target": "h1"},
        {"id": "e2", "source": "h1", "target": "e"},
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
        "nodes": _nodes_with_http(),
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


def test_post_workflow_encrypts_sensitive_http_headers_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.workflow.create = AsyncMock(return_value=_wf_row())

    resp = client.post(
        "/workflows",
        json={"name": "Sample", "nodes": _nodes_with_http(), "edges": _edges()},
    )
    assert resp.status_code == 201, resp.text

    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "http")
    assert resp_node["data"]["httpHeaders"]["Authorization"] == REDACTED_MARKER
    assert resp_node["data"]["httpHeaders"]["Content-Type"] == "application/json"

    call_kwargs = db.workflow.create.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted = next(n for n in persisted_nodes if n["type"] == "http")
    stored_auth = persisted["data"]["httpHeaders"]["Authorization"]
    assert stored_auth != "Bearer plaintext-secret-token"
    assert decrypt_marked(stored_auth) == "Bearer plaintext-secret-token"
    assert persisted["data"]["httpHeaders"]["Content-Type"] == "application/json"


def test_get_workflow_redacts_sensitive_http_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())

    resp = client.get("/workflows/w1")
    assert resp.status_code == 200, resp.text
    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "http")
    assert resp_node["data"]["httpHeaders"]["Authorization"] == REDACTED_MARKER
    assert "plaintext-secret-token" not in resp.text


def test_list_workflows_redacts_sensitive_http_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.count = AsyncMock(return_value=1)
    db.workflow.find_many = AsyncMock(return_value=[_wf_row()])

    resp = client.get("/workflows")
    assert resp.status_code == 200, resp.text
    assert "plaintext-secret-token" not in resp.text


def test_put_workflow_with_redacted_marker_preserves_stored_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.encryption import encrypt_marked

    stored_auth = encrypt_marked("original-secret-token")
    existing = _wf_row(
        nodes=_nodes_with_http(
            httpHeaders={"Authorization": stored_auth, "Content-Type": "application/json"}
        )
    )

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=existing)

    body = {
        "name": "Sample",
        "nodes": _nodes_with_http(
            httpHeaders={"Authorization": REDACTED_MARKER, "Content-Type": "application/json"}
        ),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    call_kwargs = db.workflow.update.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted = next(n for n in persisted_nodes if n["type"] == "http")
    assert persisted["data"]["httpHeaders"]["Authorization"] == stored_auth


def test_put_workflow_with_new_plaintext_header_encrypts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.encryption import encrypt_marked

    existing = _wf_row(
        nodes=_nodes_with_http(httpHeaders={"Authorization": encrypt_marked("old-token")})
    )

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=existing)

    body = {
        "name": "Sample",
        "nodes": _nodes_with_http(httpHeaders={"Authorization": "Bearer brand-new-token"}),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    call_kwargs = db.workflow.update.await_args.kwargs  # type: ignore[union-attr]
    persisted_nodes = call_kwargs["data"]["nodes"].data
    persisted = next(n for n in persisted_nodes if n["type"] == "http")
    assert (
        decrypt_marked(persisted["data"]["httpHeaders"]["Authorization"])
        == "Bearer brand-new-token"
    )
