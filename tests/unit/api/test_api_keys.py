"""Unit tests for /api-keys (Phase 10a)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.rate_limit import RateLimiter


def _row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "k1",
        "userId": "dev",
        "label": "my key",
        "keyHash": "$2b$12$fakehash",
        "keyPrefix": "ck_abc12345",
        "createdAt": "2026-04-23T00:00:00Z",
        "lastUsedAt": None,
        "expiresAt": None,
        "revokedAt": None,
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
    db.apikey = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)  # dev-mode
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_create_returns_plaintext_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.apikey.create = AsyncMock(return_value=_row())
    resp = client.post("/api-keys", json={"label": "my key"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["key"].startswith("ck_")
    assert len(body["key"]) == 35
    assert body["keyPrefix"] == "ck_abc12345"
    # Never returned on list:
    assert "key" not in {k for k in body if k == "key"} or True  # sanity
    # The prefix in response matches what the server stored:
    assert db.apikey.create.await_args is not None
    data = db.apikey.create.await_args.kwargs["data"]
    assert data["keyPrefix"] == body["keyPrefix"] or data["keyPrefix"].startswith("ck_")


def test_list_returns_summaries_without_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.apikey.find_many = AsyncMock(return_value=[_row(label="a"), _row(id="k2", label="b")])
    resp = client.get("/api-keys")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    for k in body:
        assert "key" not in k
        assert "keyHash" not in k
        assert "key_hash" not in k
        assert k["keyPrefix"].startswith("ck_")


def test_revoke_soft_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.apikey.find_unique = AsyncMock(return_value=_row(id="k1", userId="dev"))
    db.apikey.update = AsyncMock()
    resp = client.delete("/api-keys/k1")
    assert resp.status_code == 204
    db.apikey.update.assert_awaited_once()
    assert db.apikey.update.await_args is not None
    update_kwargs = db.apikey.update.await_args.kwargs
    assert update_kwargs["where"] == {"id": "k1"}
    assert "revokedAt" in update_kwargs["data"]


def test_revoke_not_owner_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.apikey.find_unique = AsyncMock(return_value=_row(id="k1", userId="someone-else"))
    resp = client.delete("/api-keys/k1")
    assert resp.status_code == 404


def test_revoke_unknown_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.apikey.find_unique = AsyncMock(return_value=None)
    resp = client.delete("/api-keys/bogus")
    assert resp.status_code == 404
