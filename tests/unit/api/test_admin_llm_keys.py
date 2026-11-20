"""Unit tests for admin LLM keys CRUD (Phase 9e)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import llm_api_keys as keys_repo
from src.db.repos.llm_api_keys import LlmApiKeyRecord, UpsertResult
from src.main import create_app
from src.security.auth import ensure_admin
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSession, override_session


def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()


def _build(monkeypatch: pytest.MonkeyPatch, *, admin: bool) -> tuple[TestClient, FakeSession]:
    _set_encryption_key(monkeypatch)
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)  # dev-mode 'dev' -> member
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    # Routes resolve their session before the admin gate runs, even for 401/403.
    session = override_session(app)
    if admin:
        app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    return TestClient(app, raise_server_exceptions=False), session


def _client_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
    return _build(monkeypatch, admin=True)


def _client_member(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
    return _build(monkeypatch, admin=False)


_NOW = datetime(2026, 4, 22, tzinfo=UTC)


def _llm_row(
    provider: str, key_prefix: str = "sk-ant", encrypted_key: str = "encrypted-ct"
) -> LlmApiKeyRecord:
    return LlmApiKeyRecord(
        id="llm-1",
        provider=provider,
        encrypted_key=encrypted_key,
        key_prefix=key_prefix,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _patch_upsert(
    monkeypatch: pytest.MonkeyPatch, provider: str, key_prefix: str, *, created: bool = True
) -> AsyncMock:
    mock = AsyncMock(
        return_value=UpsertResult(record=_llm_row(provider, key_prefix), created=created)
    )
    monkeypatch.setattr(keys_repo, "upsert", mock)
    return mock


def test_list_empty_as_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "list_keys", AsyncMock(return_value=[]))
    resp = client.get("/admin/llm-keys")
    assert resp.status_code == 200
    assert resp.json() == []
    session.commit.assert_not_called()


def test_list_returns_prefix_only_not_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "list_keys", AsyncMock(return_value=[_llm_row("anthropic")]))
    resp = client.get("/admin/llm-keys")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["provider"] == "anthropic"
    assert body[0]["key_prefix"] == "sk-ant"
    assert body[0]["updated_at"] == str(_NOW)
    assert "encryptedKey" not in body[0]
    assert "encrypted_key" not in body[0]


def test_get_returns_404_when_absent_and_summary_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=None))
    assert client.get("/admin/llm-keys/openai").status_code == 404
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=_llm_row("openai")))
    assert client.get("/admin/llm-keys/openai").json()["provider"] == "openai"


def test_member_cannot_list(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_member(monkeypatch)
    resp = client.get("/admin/llm-keys")
    assert resp.status_code == 403


def test_put_stores_encrypted_value_and_commits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    upsert = _patch_upsert(monkeypatch, "openai", "sk-pro")
    resp = client.put("/admin/llm-keys/openai", json={"value": "sk-proj-12345"})
    assert resp.status_code == 200
    assert resp.json()["key_prefix"] == "sk-pro"
    upsert.assert_awaited_once()
    kwargs = upsert.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["encrypted_key"] != "sk-proj-12345"  # stored encrypted
    assert kwargs["key_prefix"] == "sk-pro"
    session.commit.assert_awaited_once()


def test_put_updates_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    upsert = _patch_upsert(monkeypatch, "openai", "sk-new", created=False)
    resp = client.put("/admin/llm-keys/openai", json={"value": "sk-new-abcdef"})
    assert resp.status_code == 200
    upsert.assert_awaited_once()
    session.commit.assert_awaited_once()


def test_typesafe_is_an_allowed_provider() -> None:
    from src.api.admin_llm_keys import _ALLOWED_PROVIDERS  # pyright: ignore[reportPrivateUsage]

    assert "typesafe" in _ALLOWED_PROVIDERS  # pyright: ignore[reportPrivateUsage]


def test_put_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    resp = client.put("/admin/llm-keys/bogus", json={"value": "any"})
    assert resp.status_code == 422
    session.commit.assert_not_called()


def test_delete_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "delete", AsyncMock(return_value=True))
    resp = client.delete("/admin/llm-keys/openai")
    assert resp.status_code == 204
    session.commit.assert_awaited_once()


def test_delete_404_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "delete", AsyncMock(return_value=False))
    resp = client.delete("/admin/llm-keys/openai")
    assert resp.status_code == 404
    session.commit.assert_not_called()


# -- test-connection ---------------------------------------------------------


def test_test_connection_releases_the_connection_before_the_provider_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import admin_llm_keys as mod
    from src.api.admin_llm_keys_test import KeyTestResult
    from src.security.encryption import encrypt

    client, session = _client_admin(monkeypatch)
    row = _llm_row("openai", encrypted_key=encrypt("sk-real"))
    at_lookup: list[int] = []

    async def lookup(_session: object, _provider: str) -> LlmApiKeyRecord:
        at_lookup.append(session.rollback.await_count)
        return row

    monkeypatch.setattr(keys_repo, "get", lookup)
    seen: list[tuple[int, str]] = []

    async def probe(provider: str, key: str) -> KeyTestResult:
        seen.append((session.rollback.await_count, key))
        return KeyTestResult(ok=True, status=200, message="ok")

    monkeypatch.setattr(mod, "run_key_test", probe)
    resp = client.post("/admin/llm-keys/openai/test-connection")
    assert resp.status_code == 200 and resp.json()["ok"] is True
    # Rollback must come AFTER the lookup (else the lookup re-opens a transaction).
    assert at_lookup == [0]
    assert seen == [(1, "sk-real")]
    session.commit.assert_not_called()


def test_test_connection_404_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_admin(monkeypatch)
    monkeypatch.setattr(keys_repo, "get", AsyncMock(return_value=None))
    assert client.post("/admin/llm-keys/openai/test-connection").status_code == 404


# -- In-process Settings sync on PUT/DELETE ---------------------------------
#
# Closes the "save in admin UI, but workflow still says key missing"
# gap.  Without this, the boot-time sync in src/security/key_sync.py
# only catches up on the next revision restart.


def test_put_updates_in_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful PUT must immediately reflect on settings.<provider>_api_key."""
    client, _ = _client_admin(monkeypatch)
    from src.config import get_settings

    # Force the field empty so we can prove the PUT populates it.
    get_settings().anthropic_api_key = ""
    _patch_upsert(monkeypatch, "anthropic", "sk-ant")

    resp = client.put("/admin/llm-keys/anthropic", json={"value": "sk-ant-real-key-xyz"})
    assert resp.status_code == 200
    assert get_settings().anthropic_api_key == "sk-ant-real-key-xyz"


def test_put_updates_resend_in_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_admin(monkeypatch)
    from src.config import get_settings

    get_settings().resend_api_key = ""
    _patch_upsert(monkeypatch, "resend", "re_liv")

    resp = client.put("/admin/llm-keys/resend", json={"value": "re_live_123"})
    assert resp.status_code == 200
    assert get_settings().resend_api_key == "re_live_123"


def test_put_leaves_settings_unchanged_when_commit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    from src.config import get_settings

    get_settings().anthropic_api_key = "sk-ant-old"
    _patch_upsert(monkeypatch, "anthropic", "sk-ant")
    session.commit.side_effect = RuntimeError("commit failed")

    resp = client.put("/admin/llm-keys/anthropic", json={"value": "sk-ant-never-saved"})
    assert resp.status_code == 500
    assert get_settings().anthropic_api_key == "sk-ant-old"


def test_delete_clears_in_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful DELETE must immediately clear settings.<provider>_api_key.

    The next workflow attempt for that provider will then correctly report
    "key missing" instead of using a stale value.
    """
    client, _ = _client_admin(monkeypatch)
    from src.config import get_settings

    # Pre-populate the field as if the boot sync (or a prior PUT) had set it.
    get_settings().openai_api_key = "sk-openai-stale"
    monkeypatch.setattr(keys_repo, "delete", AsyncMock(return_value=True))

    resp = client.delete("/admin/llm-keys/openai")
    assert resp.status_code == 204
    assert get_settings().openai_api_key == ""


def test_delete_leaves_settings_unchanged_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = _client_admin(monkeypatch)
    from src.config import get_settings

    get_settings().openai_api_key = "sk-openai-live"
    monkeypatch.setattr(keys_repo, "delete", AsyncMock(return_value=True))
    session.commit.side_effect = RuntimeError("commit failed")

    assert client.delete("/admin/llm-keys/openai").status_code == 500
    assert get_settings().openai_api_key == "sk-openai-live"
