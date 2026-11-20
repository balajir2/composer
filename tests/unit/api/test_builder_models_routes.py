"""Routes that expose and enforce the admin-approved builder models."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import admin_builder as admin_mod
from src.api import builder as builder_mod
from src.db.repos import deployment_settings as settings_repo
from src.db.repos import llm_models as models_repo
from src.db.repos.deployment_settings import SettingRecord
from src.db.repos.llm_models import LlmModelRecord
from tests.unit.db_fakes import FakeSession, override_session


class _Limiter:
    async def check(self, route_key: str, client_key: str, config: Any) -> tuple[bool, float]:
        return True, 0.0


_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CATALOG = [
    ("anthropic", "claude-sonnet-4-5", "Sonnet"),
    ("openai", "gpt-5", "GPT-5"),
    ("groq", "llama", None),
]


def _fake_repos(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    """In-memory stand-ins for the settings and model-catalog repositories."""

    async def get(_session: Any, key: str) -> SettingRecord | None:
        return SettingRecord(key=key, value=values[key], updated_at=_NOW) if key in values else None

    async def upsert(_session: Any, key: str, value: str) -> SettingRecord:
        values[key] = value
        return SettingRecord(key=key, value=value, updated_at=_NOW)

    async def list_models(
        _session: Any, *, provider: str | None = None, enabled_only: bool = False
    ) -> list[LlmModelRecord]:
        return [
            LlmModelRecord(
                id=f"{p}-{m}",
                provider=p,
                model_id=m,
                label=label,
                enabled=True,
                verification_status=None,
                verification_message=None,
                verified_at=None,
                created_at=_NOW,
                updated_at=_NOW,
            )
            for p, m, label in _CATALOG
        ]

    monkeypatch.setattr(settings_repo, "get", get)
    monkeypatch.setattr(settings_repo, "upsert", upsert)
    monkeypatch.setattr(models_repo, "list_models", list_models)


class _One:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def find_unique(self, **_kwargs: Any) -> Any:
        return self._row


def _db() -> Any:
    wf = SimpleNamespace(id="w1", userId="dev", name="n", description=None, nodes=[], edges=[])
    return SimpleNamespace(workflow=_One(wf), workflowassignment=_One(None))


def _client(
    monkeypatch: pytest.MonkeyPatch, settings: dict[str, str], role: str = "member"
) -> tuple[TestClient, FakeSession]:
    _fake_repos(monkeypatch, settings)
    db = _db()
    app = FastAPI()
    app.include_router(builder_mod.router)
    app.include_router(admin_mod.router)
    app.state.builder_sessions = {}

    async def fake_db() -> Any:
        return db

    async def fake_role() -> tuple[str, str]:
        return "dev", role

    async def fake_user() -> str:
        return "dev"

    async def fake_admin() -> str:
        if role != "admin":
            raise HTTPException(status_code=403, detail="admin role required")
        return "dev"

    app.dependency_overrides[builder_mod.get_db] = fake_db
    session = override_session(app)
    app.dependency_overrides[builder_mod.get_current_role] = fake_role
    app.dependency_overrides[builder_mod.get_current_user_id] = fake_user
    app.dependency_overrides[admin_mod.ensure_admin] = fake_admin
    app.dependency_overrides[builder_mod.get_rate_limiter] = lambda: _Limiter()
    return TestClient(app), session


def test_member_sees_default_plus_admin_allowed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client(monkeypatch, {"builder_allowed_models": '["openai/gpt-5"]'})

    body = client.get("/builder/models").json()

    assert body["default"] == "anthropic/claude-sonnet-4-5"
    assert [m["id"] for m in body["models"]] == ["anthropic/claude-sonnet-4-5", "openai/gpt-5"]
    assert body["models"][1]["label"] == "GPT-5"
    session.commit.assert_not_called()


def test_message_with_a_model_the_admin_did_not_allow_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(monkeypatch, {})
    resp = client.post("/builder/w1/message", json={"message": "hi", "model": "groq/llama"})

    assert resp.status_code == 400
    assert "groq/llama" in resp.json()["detail"]


def test_chosen_allowed_model_is_the_one_built(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []

    def fake_build(model_string: str) -> object:
        built.append(model_string)
        raise RuntimeError("stop after model selection")

    monkeypatch.setattr(builder_mod, "build_chat_model", fake_build)
    base, _ = _client(monkeypatch, {"builder_allowed_models": '["openai/gpt-5"]'})
    client = TestClient(base.app, raise_server_exceptions=False)

    client.post("/builder/w1/message", json={"message": "hi", "model": "openai/gpt-5"})

    assert built == ["openai/gpt-5"]


def test_admin_can_read_and_update_the_allowed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[str, str] = {}
    client, session = _client(monkeypatch, values, role="admin")

    put = client.put(
        "/admin/builder-models",
        json={"defaultModel": "anthropic/claude-sonnet-4-5", "allowedModels": ["openai/gpt-5"]},
    )

    assert put.status_code == 200
    assert put.json() == {
        "defaultModel": "anthropic/claude-sonnet-4-5",
        "allowedModels": ["anthropic/claude-sonnet-4-5", "openai/gpt-5"],
    }
    assert values["builder_allowed_models"] == '["openai/gpt-5"]'
    session.commit.assert_awaited_once()
    assert client.get("/admin/builder-models").json() == put.json()
    session.commit.assert_awaited_once()  # the read did not commit


def test_admin_cannot_allow_a_model_missing_from_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = _client(monkeypatch, {}, role="admin")
    resp = client.put(
        "/admin/builder-models",
        json={"defaultModel": "anthropic/claude-sonnet-4-5", "allowedModels": ["x/nope"]},
    )

    assert resp.status_code == 400
    assert "x/nope" in resp.json()["detail"]
    session.commit.assert_not_called()


def test_members_cannot_change_the_allowed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client(monkeypatch, {}, role="member")
    resp = client.put(
        "/admin/builder-models",
        json={"defaultModel": "anthropic/claude-sonnet-4-5", "allowedModels": []},
    )

    assert resp.status_code == 403
    session.commit.assert_not_called()
