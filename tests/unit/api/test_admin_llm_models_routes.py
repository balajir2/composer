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
from src.main import create_app
from src.security.auth import ensure_admin
from src.security.encryption import encrypt

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
) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": model_id,
        "provider": provider,
        "modelId": "gpt-x",
        "label": "GPT X",
        "enabled": enabled,
        "verificationStatus": None,
        "verificationMessage": None,
        "verifiedAt": None,
        "createdAt": _NOW,
        "updatedAt": _NOW,
    }
    base.update(extra)
    return SimpleNamespace(**base)


def _build(
    monkeypatch: pytest.MonkeyPatch, *, admin: bool, environment: str = "development"
) -> tuple[TestClient, MagicMock]:
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
    if admin:
        app.dependency_overrides[ensure_admin] = lambda: "admin-1"
    return TestClient(app), db


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
    client, db = _build(monkeypatch, admin=False)
    resp = client.request(method, path, json=body)
    assert resp.status_code == 403
    db.llmmodel.create.assert_not_called()
    db.llmmodel.delete.assert_not_called()


@pytest.mark.parametrize(("method", "path", "body"), _ROUTES)
def test_admin_model_routes_reject_unauthenticated_requests(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: dict[str, Any] | None
) -> None:
    client, _ = _build(monkeypatch, admin=False, environment="production")
    resp = client.request(method, path, json=body)
    assert resp.status_code == 401


# ─── CRUD ───────────────────────────────────────────────────────────────────


def test_list_returns_all_models_ordered_by_provider_then_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _build(monkeypatch, admin=True)
    db.llmmodel.find_many = AsyncMock(return_value=[_row("a"), _row("b", enabled=False)])
    resp = client.get("/admin/llm-models")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()] == ["a", "b"]
    assert _kwargs(db.llmmodel.find_many)["order"] == [
        {"provider": "asc"},
        {"modelId": "asc"},
    ]


def test_create_model_persists_fields_and_returns_201(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _build(monkeypatch, admin=True)
    db.llmmodel.create = AsyncMock(return_value=_row())
    resp = client.post(
        "/admin/llm-models", json={"provider": "openai", "modelId": "gpt-x", "label": "GPT X"}
    )
    assert resp.status_code == 201
    assert _kwargs(db.llmmodel.create)["data"] == {
        "provider": "openai",
        "modelId": "gpt-x",
        "label": "GPT X",
        "enabled": True,
    }


def test_create_duplicate_model_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _build(monkeypatch, admin=True)
    db.llmmodel.create = AsyncMock(side_effect=RuntimeError("Unique constraint failed"))
    resp = client.post("/admin/llm-models", json={"provider": "openai", "modelId": "gpt-x"})
    assert resp.status_code == 409


def test_update_only_applies_supplied_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _build(monkeypatch, admin=True)
    db.llmmodel.find_unique = AsyncMock(return_value=_row())
    db.llmmodel.update = AsyncMock(return_value=_row(enabled=False))
    resp = client.patch("/admin/llm-models/m1", json={"enabled": False})
    assert resp.status_code == 200
    assert _kwargs(db.llmmodel.update)["data"] == {"enabled": False}


def test_update_missing_model_returns_404_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _build(monkeypatch, admin=True)
    db.llmmodel.find_unique = AsyncMock(return_value=None)
    db.llmmodel.update = AsyncMock()
    resp = client.patch("/admin/llm-models/nope", json={"label": "x"})
    assert resp.status_code == 404
    db.llmmodel.update.assert_not_called()


def test_delete_removes_model_and_returns_204(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _build(monkeypatch, admin=True)
    db.llmmodel.find_unique = AsyncMock(return_value=_row())
    db.llmmodel.delete = AsyncMock()
    resp = client.delete("/admin/llm-models/m1")
    assert resp.status_code == 204
    db.llmmodel.delete.assert_awaited_once_with(where={"id": "m1"})


def test_delete_missing_model_returns_404_without_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _build(monkeypatch, admin=True)
    db.llmmodel.find_unique = AsyncMock(return_value=None)
    db.llmmodel.delete = AsyncMock()
    assert client.delete("/admin/llm-models/nope").status_code == 404
    db.llmmodel.delete.assert_not_called()


# ─── verify ─────────────────────────────────────────────────────────────────


def _verify_setup(
    monkeypatch: pytest.MonkeyPatch,
    status: VerifyStatus,
    *,
    row: SimpleNamespace | None,
    key_row: SimpleNamespace | None,
    updated: SimpleNamespace | None = None,
) -> tuple[TestClient, MagicMock, _FakeVerify]:
    client, db = _build(monkeypatch, admin=True)
    fake = _FakeVerify(status)
    monkeypatch.setattr(mod, "run_model_verify", fake)
    db.llmmodel.find_unique = AsyncMock(return_value=row)
    db.llmapikey.find_unique = AsyncMock(return_value=key_row)
    db.llmmodel.update = AsyncMock(return_value=updated or row)
    return client, db, fake


def test_verify_missing_model_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, fake = _verify_setup(monkeypatch, "ok", row=None, key_row=None)
    assert client.post("/admin/llm-models/m1/verify").status_code == 404
    assert fake.calls == []


def test_verify_without_configured_provider_key_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, fake = _verify_setup(monkeypatch, "ok", row=_row(), key_row=None)
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 400
    assert "No API key configured" in resp.json()["detail"]
    assert fake.calls == []


def test_verify_undecryptable_key_returns_500_without_leaking_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_row = SimpleNamespace(encryptedKey="not-valid-ciphertext")
    client, _, fake = _verify_setup(monkeypatch, "ok", row=_row(), key_row=key_row)
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 500
    assert "failed to decrypt openai key" in resp.json()["detail"]
    assert fake.calls == []


def test_verify_ok_stamps_row_and_never_returns_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    updated = _row(verificationStatus="ok", verificationMessage="probe-msg", verifiedAt=_NOW)
    client, db, fake = _verify_setup(
        monkeypatch, "ok", row=_row(), key_row=key_row, updated=updated
    )
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["auto_disabled"] is False
    assert fake.calls == [("openai", "gpt-x", _PLAINTEXT_KEY)]
    assert _PLAINTEXT_KEY not in resp.text
    data = _kwargs(db.llmmodel.update)["data"]
    assert data["verificationStatus"] == "ok"
    assert "enabled" not in data


def test_verify_unavailable_auto_disables_enabled_model(monkeypatch: pytest.MonkeyPatch) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    client, db, _ = _verify_setup(
        monkeypatch,
        "unavailable",
        row=_row(enabled=True),
        key_row=key_row,
        updated=_row(enabled=False, verificationStatus="unavailable"),
    )
    body = client.post("/admin/llm-models/m1/verify").json()
    assert body["auto_disabled"] is True
    assert _kwargs(db.llmmodel.update)["data"]["enabled"] is False


def test_verify_unavailable_on_already_disabled_model_does_not_report_auto_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    client, db, _ = _verify_setup(
        monkeypatch, "unavailable", row=_row(enabled=False), key_row=key_row
    )
    body = client.post("/admin/llm-models/m1/verify").json()
    assert body["auto_disabled"] is False
    assert "enabled" not in _kwargs(db.llmmodel.update)["data"]


@pytest.mark.parametrize("status", ["auth_error", "error"])
def test_verify_key_or_network_problems_do_not_touch_the_row(
    monkeypatch: pytest.MonkeyPatch, status: VerifyStatus
) -> None:
    key_row = SimpleNamespace(encryptedKey=_encrypted(monkeypatch))
    client, db, _ = _verify_setup(monkeypatch, status, row=_row(), key_row=key_row)
    resp = client.post("/admin/llm-models/m1/verify")
    assert resp.status_code == 200
    assert resp.json()["status"] == status
    db.llmmodel.update.assert_not_called()


def _encrypted(monkeypatch: pytest.MonkeyPatch) -> str:
    from src.config import get_settings

    monkeypatch.setenv("ENCRYPTION_KEY", _ENC_KEY)
    get_settings.cache_clear()
    return encrypt(_PLAINTEXT_KEY)
