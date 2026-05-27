"""Unit tests for admin LLM keys CRUD (Phase 9e)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.auth import ensure_admin
from src.security.rate_limit import RateLimiter


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()


def _client_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    _set_encryption_key(monkeypatch)
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.llmapikey = MagicMock()
    db.user = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()

    # Override ensure_admin so the MagicMock db doesn't block admin access
    app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]

    return TestClient(app), db


def _client_member(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    _set_encryption_key(monkeypatch)
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.llmapikey = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)  # dev-mode 'dev' → member
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    # No dependency_overrides → get_current_role returns 'member' → 403
    return TestClient(app), db


def _llm_row(provider: str, key_prefix: str = "sk-ant") -> SimpleNamespace:
    return SimpleNamespace(
        id="llm-1",
        provider=provider,
        encryptedKey="encrypted-ct",
        keyPrefix=key_prefix,
        createdAt="2026-04-22T00:00:00Z",
        updatedAt="2026-04-22T00:00:00Z",
    )


def test_list_empty_as_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_admin(monkeypatch)
    db.llmapikey.find_many = AsyncMock(return_value=[])
    resp = client.get("/admin/llm-keys")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_returns_prefix_only_not_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_admin(monkeypatch)
    db.llmapikey.find_many = AsyncMock(return_value=[_llm_row("anthropic")])
    resp = client.get("/admin/llm-keys")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["provider"] == "anthropic"
    assert body[0]["key_prefix"] == "sk-ant"
    assert "encryptedKey" not in body[0]
    assert "encrypted_key" not in body[0]


def test_member_cannot_list(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_member(monkeypatch)
    resp = client.get("/admin/llm-keys")
    assert resp.status_code == 403


def test_put_creates_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_admin(monkeypatch)
    db.llmapikey.find_unique = AsyncMock(return_value=None)
    db.llmapikey.create = AsyncMock(return_value=_llm_row("openai", key_prefix="sk-pro"))
    resp = client.put("/admin/llm-keys/openai", json={"value": "sk-proj-12345"})
    assert resp.status_code == 200
    db.llmapikey.create.assert_awaited_once()
    # key stored encrypted (value != plaintext)
    assert db.llmapikey.create.await_args is not None
    data = db.llmapikey.create.await_args.kwargs["data"]
    assert data["encryptedKey"] != "sk-proj-12345"
    assert data["keyPrefix"] == "sk-pro"


def test_put_updates_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_admin(monkeypatch)
    db.llmapikey.find_unique = AsyncMock(return_value=_llm_row("openai"))
    db.llmapikey.update = AsyncMock(return_value=_llm_row("openai", key_prefix="sk-new"))
    resp = client.put("/admin/llm-keys/openai", json={"value": "sk-new-abcdef"})
    assert resp.status_code == 200
    db.llmapikey.update.assert_awaited_once()


def test_put_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_admin(monkeypatch)
    resp = client.put("/admin/llm-keys/bogus", json={"value": "any"})
    assert resp.status_code == 422


def test_delete_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_admin(monkeypatch)
    db.llmapikey.find_unique = AsyncMock(return_value=_llm_row("openai"))
    db.llmapikey.delete = AsyncMock()
    resp = client.delete("/admin/llm-keys/openai")
    assert resp.status_code == 204


def test_delete_404_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_admin(monkeypatch)
    db.llmapikey.find_unique = AsyncMock(return_value=None)
    resp = client.delete("/admin/llm-keys/openai")
    assert resp.status_code == 404


# ── In-process Settings sync on PUT/DELETE ─────────────────────────
#
# Closes the "save in admin UI, but workflow still says key missing"
# gap.  Without this, the boot-time sync in src/security/key_sync.py
# only catches up on the next revision restart.


def test_put_updates_in_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful PUT must immediately reflect on settings.<provider>_api_key."""
    client, db = _client_admin(monkeypatch)
    from src.config import get_settings

    # Force the field empty so we can prove the PUT populates it.
    get_settings().anthropic_api_key = ""

    db.llmapikey.find_unique = AsyncMock(return_value=None)
    db.llmapikey.create = AsyncMock(return_value=_llm_row("anthropic", key_prefix="sk-ant"))

    resp = client.put("/admin/llm-keys/anthropic", json={"value": "sk-ant-real-key-xyz"})
    assert resp.status_code == 200
    assert get_settings().anthropic_api_key == "sk-ant-real-key-xyz"


def test_delete_clears_in_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful DELETE must immediately clear settings.<provider>_api_key.

    The next workflow attempt for that provider will then correctly report
    "key missing" instead of using a stale value.
    """
    client, db = _client_admin(monkeypatch)
    from src.config import get_settings

    # Pre-populate the field as if the boot sync (or a prior PUT) had set it.
    get_settings().openai_api_key = "sk-openai-stale"

    db.llmapikey.find_unique = AsyncMock(return_value=_llm_row("openai"))
    db.llmapikey.delete = AsyncMock()

    resp = client.delete("/admin/llm-keys/openai")
    assert resp.status_code == 204
    assert get_settings().openai_api_key == ""
