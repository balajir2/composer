"""Tests for vector-db node apiKey/embeddingApiKey encryption-at-rest and
redaction-on-read (P0-5).

Mirrors tests/unit/api/test_workflows_jira_tokens.py's pattern exactly —
same class of gap, same fix, applied to the vector-db node's two
credential fields instead of Jira's single apiToken.
"""

from typing import Any
from unittest.mock import MagicMock

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


def _vector_db_node(**data_overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": "Vector DB",
        "vectorDbProvider": "pinecone",
        "vectorDbEndpoint": "https://example.pinecone.io",
        "vectorDbApiKey": "plaintext-pinecone-key",
        "vectorDbCollection": "docs",
        "vectorDbEmbeddingApiKey": "plaintext-openai-key",
        "vectorDbQueryPrompt": "find things",
    }
    data.update(data_overrides)
    return {"id": "v1", "type": "vector-db", "position": {"x": 0, "y": 0}, "data": data}


def _nodes_with_vector_db(**data_overrides: Any) -> list[dict[str, Any]]:
    return [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        _vector_db_node(**data_overrides),
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ]


def _edges() -> list[dict[str, Any]]:
    return [
        {"id": "e1", "source": "s", "target": "v1"},
        {"id": "e2", "source": "v1", "target": "e"},
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
        "nodes": _nodes_with_vector_db(),
        "edges": _edges(),
        "version": None,
        "is_template": False,
        "is_public": False,
        "created_at": "2026-04-20T00:00:00Z",
        "updated_at": "2026-04-20T00:00:00Z",
    }
    base.update(overrides)
    return make_workflow(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, WorkflowRepoMocks]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), repos


def test_post_workflow_encrypts_vector_db_keys_before_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch)
    repos.create.return_value = _wf_row()

    resp = client.post(
        "/workflows",
        json={"name": "Sample", "nodes": _nodes_with_vector_db(), "edges": _edges()},
    )
    assert resp.status_code == 201, resp.text

    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "vector-db")
    assert resp_node["data"]["vectorDbApiKey"] == REDACTED_MARKER
    assert resp_node["data"]["vectorDbEmbeddingApiKey"] == REDACTED_MARKER

    persisted_nodes = repos.create.await_args.args[1]["nodes"]
    persisted = next(n for n in persisted_nodes if n["type"] == "vector-db")
    stored_key = persisted["data"]["vectorDbApiKey"]
    stored_embedding_key = persisted["data"]["vectorDbEmbeddingApiKey"]
    assert stored_key != "plaintext-pinecone-key"
    assert stored_embedding_key != "plaintext-openai-key"
    assert decrypt_marked(stored_key) == "plaintext-pinecone-key"
    assert decrypt_marked(stored_embedding_key) == "plaintext-openai-key"


def test_get_workflow_redacts_vector_db_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)
    repos.get.return_value = _wf_row()

    resp = client.get("/workflows/w1")
    assert resp.status_code == 200, resp.text
    resp_node = next(n for n in resp.json()["nodes"] if n["type"] == "vector-db")
    assert resp_node["data"]["vectorDbApiKey"] == REDACTED_MARKER
    assert resp_node["data"]["vectorDbEmbeddingApiKey"] == REDACTED_MARKER
    assert "plaintext-pinecone-key" not in resp.text
    assert "plaintext-openai-key" not in resp.text


def test_list_workflows_redacts_vector_db_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch)
    repos.list_page.return_value = ([_wf_row()], 1)

    resp = client.get("/workflows")
    assert resp.status_code == 200, resp.text
    assert "plaintext-pinecone-key" not in resp.text
    assert "plaintext-openai-key" not in resp.text


def test_put_workflow_with_redacted_marker_preserves_stored_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.encryption import encrypt_marked

    stored_key = encrypt_marked("original-pinecone-key")
    stored_embedding_key = encrypt_marked("original-openai-key")
    existing = _wf_row(
        nodes=_nodes_with_vector_db(
            vectorDbApiKey=stored_key, vectorDbEmbeddingApiKey=stored_embedding_key
        )
    )

    client, repos = _client(monkeypatch)
    repos.get.return_value = existing
    repos.update.return_value = existing

    body = {
        "name": "Sample",
        "nodes": _nodes_with_vector_db(
            vectorDbApiKey=REDACTED_MARKER, vectorDbEmbeddingApiKey=REDACTED_MARKER
        ),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    persisted_nodes = repos.update.await_args.args[2]["nodes"]
    persisted = next(n for n in persisted_nodes if n["type"] == "vector-db")
    assert persisted["data"]["vectorDbApiKey"] == stored_key
    assert persisted["data"]["vectorDbEmbeddingApiKey"] == stored_embedding_key


def test_put_workflow_with_new_plaintext_keys_encrypts_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.security.encryption import encrypt_marked

    existing = _wf_row(
        nodes=_nodes_with_vector_db(
            vectorDbApiKey=encrypt_marked("old-key"),
            vectorDbEmbeddingApiKey=encrypt_marked("old-embedding-key"),
        )
    )

    client, repos = _client(monkeypatch)
    repos.get.return_value = existing
    repos.update.return_value = existing

    body = {
        "name": "Sample",
        "nodes": _nodes_with_vector_db(
            vectorDbApiKey="brand-new-key", vectorDbEmbeddingApiKey="brand-new-embedding-key"
        ),
        "edges": _edges(),
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text

    persisted_nodes = repos.update.await_args.args[2]["nodes"]
    persisted = next(n for n in persisted_nodes if n["type"] == "vector-db")
    assert decrypt_marked(persisted["data"]["vectorDbApiKey"]) == "brand-new-key"
    assert decrypt_marked(persisted["data"]["vectorDbEmbeddingApiKey"]) == "brand-new-embedding-key"
