"""Behavioral tests for the admin LLM model catalog routes
(src/api/admin_llm_models.py): authorization, CRUD, and the verify flow."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api import admin_llm_models as mod
from src.api.admin_llm_models_verify import ModelVerifyResult, VerifyStatus
from src.db.errors import AlreadyExists
from src.db.repos import llm_models as models_repo
from src.db.repos.common import UNSET
from src.db.repos.llm_models import LlmModelRecord
from src.main import create_app
from src.security.auth import ensure_admin
from src.security.encryption import encrypt
from tests.unit.db_fakes import FakeSession, override_session

_ENC_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_PLAINTEXT_KEY = "sk-provider-plaintext-secret"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _kwargs(mock: Any) -> dict[str, Any]:
    args = mock.await_args
    assert args is not None
    return cast("dict[str, Any]", args.kwargs)


def _row(
    model_id: str = "m1",
    provider: str = "openai",
    enabled: bool = True,
    **extra: Any,
) -> LlmModelRecord:
    base: dict[str, Any] = {
        "id": model_id,
        "provider": provider,
        "model_id": "gpt-x",
        "label": "GPT X",
        "enabled": enabled,
        "verification_status": None,
        "verification_message": None,
        "verified_at": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(extra)
    return LlmModelRecord(**base)


def _build(
    monkeypatch: pytest.MonkeyPatch, *, admin: bool, environment: str = "development"
) -> tuple[TestClient, MagicMock, FakeSession]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("ENCRYPTION_KEY", _ENC_KEY)
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)  # dev user -> member
    app.state.db = db
    app.state.checkpointer = MagicMock()
    # Routes resolve their session before the admin gate runs, even for 401/403.
    session = override_session(app)
    if admin:
        app.dependency_overrides[ensure_admin] = lambda: "admin-1"
    return TestClient(app, raise_server_exceptions=False), db, session


class _FakeVerify:
    def __init__(self, status: VerifyStatus, message: str = "probe-msg") -> None:
        self.status: VerifyStatus = status
        self.message = message
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, provider: str, model_id: str, key: str) -> ModelVerifyResult:
        self.calls.append((provider, model_id, key))
        return ModelVerifyResult(status=self.status, http_status=200, message=self.message)


# ─── authorization ──────────────────────────────────────────────────────────

_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/admin/llm-models", None),
    ("POST", "/admin/llm-models", {"provider": "openai", "modelId": "x"}),
    ("PATCH", "/admin/llm-models/m1", {"enabled": False}),
    ("POST", "/admin/llm-models/m1/verify", None),
    ("DELETE", "/admin/llm-models/m1", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _ROUTES)
def test_admin_model_routes_forbid_non_admin_members(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: dict[str, Any] | None
) -> None:
    client, _, session = _build(monkeypatch, admin=False)
    create = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(models_repo, "create", create)
    monkeypatch.setattr(models_repo, "delete", delete)
    resp = client.request(method, path, json=body)
    assert resp.status_code == 403
    create.assert_not_called()
    delete.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize(("method", "path", "body"), _ROUTES)
def test_admin_model_routes_reject_unauthenticated_requests(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: dict[str, Any] | None
) -> None:
    client, _, _ = _build(monkeypatch, admin=False, environment="production")
    resp = client.request(method, path, json=body)
    assert resp.status_code == 401


# ─── CRUD ───────────────────────────────────────────────────────────────────


def test_list_returns_all_models_ordered_by_provider_then_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    list_models = AsyncMock(return_value=[_row("a"), _row("b", enabled=False)])
    monkeypatch.setattr(models_repo, "list_models", list_models)
    resp = client.get("/admin/llm-models")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()] == ["a", "b"]
    list_models.assert_awaited_once_with(session)  # repo orders by provider, model_id
    session.commit.assert_not_called()


def test_create_model_persists_fields_and_returns_201(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    create = AsyncMock(return_value=_row())
    monkeypatch.setattr(models_repo, "create", create)
    resp = client.post(
        "/admin/llm-models", json={"provider": "openai", "modelId": "gpt-x", "label": "GPT X"}
    )
    assert resp.status_code == 201
    assert resp.json()["modelId"] == "gpt-x"
    assert _kwargs(create) == {
        "provider": "openai",
        "model_id": "gpt-x",
        "label": "GPT X",
        "enabled": True,
    }
    session.commit.assert_awaited_once()


def test_create_duplicate_model_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    monkeypatch.setattr(
        models_repo,
        "create",
        AsyncMock(side_effect=AlreadyExists("llm_models_provider_model_id_key")),
    )
    resp = client.post("/admin/llm-models", json={"provider": "openai", "modelId": "gpt-x"})
    assert resp.status_code == 409
    assert "Model already exists for provider" in resp.json()["detail"]
    session.commit.assert_not_called()


def test_create_non_duplicate_error_is_a_500_not_a_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    monkeypatch.setattr(models_repo, "create", AsyncMock(side_effect=RuntimeError("db down")))
    resp = client.post("/admin/llm-models", json={"provider": "openai", "modelId": "gpt-x"})
    assert resp.status_code == 500
    session.commit.assert_not_called()


def test_update_only_applies_supplied_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    update = AsyncMock(return_value=_row(enabled=False))
    monkeypatch.setattr(models_repo, "update", update)
    resp = client.patch("/admin/llm-models/m1", json={"enabled": False})
    assert resp.status_code == 200
    assert _kwargs(update) == {"label": UNSET, "enabled": False}
    session.commit.assert_awaited_once()


def test_update_missing_model_returns_404_without_committing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    monkeypatch.setattr(models_repo, "update", AsyncMock(return_value=None))
    resp = client.patch("/admin/llm-models/nope", json={"label": "x"})
    assert resp.status_code == 404
    session.commit.assert_not_called()


def test_delete_removes_model_and_returns_204(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(models_repo, "delete", delete)
    resp = client.delete("/admin/llm-models/m1")
    assert resp.status_code == 204
    delete.assert_awaited_once_with(session, "m1")
    session.commit.assert_awaited_once()


def test_delete_missing_model_returns_404_without_committing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, session = _build(monkeypatch, admin=True)
    monkeypatch.setattr(models_repo, "delete", AsyncMock(return_value=False))
    assert client.delete("/admin/llm-models/nope").status_code == 404
    session.commit.assert_not_called()


# ─── verify ─────────────────────────────────────────────────────────────────


def _verify_setup(
    monkeypatch: pytest.MonkeyPatch,
    status: VerifyStatus,
    *,
    row: LlmModelRecord | None,
    key_row: SimpleNamespace | None,
    updated: LlmModelRecord | None = None,
) -> tuple[TestClient, AsyncMock, FakeSession, _FakeVerify]:
    client, db, session = _build(monkeypatch, admin=True)
    fake = _FakeVerify(status)
    monkeypatch.setattr(mod, "run_model_verify", fake)
    monkeypatch.setattr(models_repo, "get_by_id", AsyncMock(return_value=row))
    record = AsyncMock(return_value=updated or row)
    monkeypatch.setattr(models_repo, "record_verification", record)
    # The key lookup stays on Prisma until the LLM API keys task.
    db.llmapikey.find_unique = AsyncMock(return_value=key_row)
    return client, record, session, fake


def test_verify_missing_model_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _, fake = _verify_setup(monkeypatch, "ok", row=None, key_row=None)
    assert client.post("/admin/llm-models/m1/verify").status_code == 404
    assert fake.calls == []


def test_verify_without_configured_provider_key_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _, fake = _verify_setup(monkeypatch, "ok", row=_row(), key_row=None)
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 400
    assert "No API key configured" in resp.json()["detail"]
    assert fake.calls == []


def test_verify_undecryptable_key_returns_500_without_leaking_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_row = SimpleNamespace(encryptedKey="not-valid-ciphertext")
    client, _, _, fake = _verify_setup(monkeypatch, "ok", row=_row(), key_row=key_row)
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 500
    assert "failed to decrypt openai key" in resp.json()["detail"]
    assert fake.calls == []


def test_verify_ok_stamps_row_and_never_returns_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    updated = _row(verification_status="ok", verification_message="probe-msg", verified_at=_NOW)
    client, record, session, fake = _verify_setup(
        monkeypatch, "ok", row=_row(), key_row=key_row, updated=updated
    )
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["auto_disabled"] is False
    assert body["model"]["verificationStatus"] == "ok"
    assert fake.calls == [("openai", "gpt-x", _PLAINTEXT_KEY)]
    assert _PLAINTEXT_KEY not in resp.text
    kw = _kwargs(record)
    assert (kw["status"], kw["message"], kw["disable"]) == ("ok", "probe-msg", False)
    assert kw["verified_at"].tzinfo is not None
    session.commit.assert_awaited_once()


def test_verify_unavailable_auto_disables_enabled_model(monkeypatch: pytest.MonkeyPatch) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    client, record, session, _ = _verify_setup(
        monkeypatch,
        "unavailable",
        row=_row(enabled=True),
        key_row=key_row,
        updated=_row(enabled=False, verification_status="unavailable"),
    )
    body = client.post("/admin/llm-models/m1/verify").json()
    assert body["auto_disabled"] is True
    assert _kwargs(record)["disable"] is True
    session.commit.assert_awaited_once()


def test_verify_unavailable_on_already_disabled_model_does_not_report_auto_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    client, record, _, _ = _verify_setup(
        monkeypatch, "unavailable", row=_row(enabled=False), key_row=key_row
    )
    body = client.post("/admin/llm-models/m1/verify").json()
    assert body["auto_disabled"] is False
    assert _kwargs(record)["disable"] is False


@pytest.mark.parametrize("status", ["auth_error", "error"])
def test_verify_key_or_network_problems_do_not_touch_the_row(
    monkeypatch: pytest.MonkeyPatch, status: VerifyStatus
) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    client, record, session, _ = _verify_setup(monkeypatch, status, row=_row(), key_row=key_row)
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 200
    assert resp.json()["status"] == status
    record.assert_not_called()
    session.commit.assert_not_called()


def _encrypted(monkeypatch: pytest.MonkeyPatch) -> str:
    from src.config import get_settings

    monkeypatch.setenv("ENCRYPTION_KEY", _ENC_KEY)
    get_settings.cache_clear()
    return encrypt(_PLAINTEXT_KEY)
