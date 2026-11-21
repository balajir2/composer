"""Unit tests for /api-keys (Phase 10a)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import api_keys as keys_repo
from src.db.repos.api_keys import ApiKeyRecord
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSession, override_session


def _row(**overrides: Any) -> ApiKeyRecord:
    base: dict[str, Any] = {
        "id": "k1",
        "user_id": "dev",
        "label": "my key",
        "key_hash": "$2b$12$fakehash",
        "key_prefix": "ck_abc12345",
        "created_at": datetime(2026, 4, 23, tzinfo=UTC),
        "last_used_at": None,
        "expires_at": None,
        "revoked_at": None,
    }
    base.update(overrides)
    return ApiKeyRecord(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    session = override_session(app)
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), session


def test_create_returns_plaintext_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client(monkeypatch)
    create = AsyncMock(return_value=_row())
    monkeypatch.setattr(keys_repo, "create", create)
    resp = client.post("/api-keys", json={"label": "my key"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["key"].startswith("ck_")
    assert len(body["key"]) == 35
    assert body["keyPrefix"] == "ck_abc12345"
    kwargs = create.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["user_id"] == "dev"
    assert kwargs["key_prefix"].startswith("ck_")
    assert kwargs["key_hash"] != body["key"]  # only the hash is stored
    assert session.commit.await_count == 1


def test_create_with_naive_expires_at_passes_aware_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch)
    create = AsyncMock(return_value=_row())
    monkeypatch.setattr(keys_repo, "create", create)
    resp = client.post("/api-keys", json={"label": "k", "expiresAt": "2099-01-01T00:00:00"})
    assert resp.status_code == 201
    expires = create.await_args.kwargs["expires_at"]  # type: ignore[union-attr]
    assert expires == datetime(2099, 1, 1, tzinfo=UTC)
    assert expires.tzinfo is UTC


def test_list_returns_summaries_without_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(
        keys_repo,
        "list_for_user",
        AsyncMock(return_value=[_row(label="a"), _row(id="k2", label="b")]),
    )
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
    client, session = _client(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=_row()))
    revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(keys_repo, "revoke_if_active", revoke)
    resp = client.delete("/api-keys/k1")
    assert resp.status_code == 204
    revoke.assert_awaited_once()
    assert revoke.await_args.args[1] == "k1"  # type: ignore[union-attr]
    assert session.commit.await_count == 1


def test_revoke_already_revoked_is_noop_204(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client(monkeypatch)
    monkeypatch.setattr(
        keys_repo, "get", AsyncMock(return_value=_row(revoked_at=datetime(2026, 4, 24, tzinfo=UTC)))
    )
    monkeypatch.setattr(keys_repo, "revoke_if_active", AsyncMock(return_value=False))
    assert client.delete("/api-keys/k1").status_code == 204
    assert session.commit.await_count == 0


def test_revoke_not_owner_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=_row(user_id="someone-else")))
    revoke = AsyncMock()
    monkeypatch.setattr(keys_repo, "revoke_if_active", revoke)
    assert client.delete("/api-keys/k1").status_code == 404
    revoke.assert_not_awaited()


def test_revoke_unknown_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=None))
    assert client.delete("/api-keys/bogus").status_code == 404
