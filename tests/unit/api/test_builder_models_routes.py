"""Routes that expose and enforce the admin-approved builder models."""

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import admin_builder as admin_mod
from src.api import builder as builder_mod


class _Limiter:
    async def check(self, route_key: str, client_key: str, config: Any) -> tuple[bool, float]:
        return True, 0.0


class _Settings:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def find_unique(self, *, where: dict[str, str]) -> Any:
        key = where["key"]
        return SimpleNamespace(key=key, value=self.values[key]) if key in self.values else None

    async def upsert(self, *, where: dict[str, str], data: dict[str, Any]) -> None:
        self.values[where["key"]] = data["create"]["value"]


class _Models:
    async def find_many(self, **_kwargs: Any) -> list[Any]:
        rows = [
            ("anthropic", "claude-sonnet-4-5", "Sonnet"),
            ("openai", "gpt-5", "GPT-5"),
            ("groq", "llama", None),
        ]
        return [
            SimpleNamespace(provider=p, modelId=m, label=lbl, enabled=True) for p, m, lbl in rows
        ]


class _One:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def find_unique(self, **_kwargs: Any) -> Any:
        return self._row


def _db(settings: dict[str, str]) -> Any:
    wf = SimpleNamespace(id="w1", userId="dev", name="n", description=None, nodes=[], edges=[])
    return SimpleNamespace(
        deploymentsetting=_Settings(settings),
        llmmodel=_Models(),
        workflow=_One(wf),
        workflowassignment=_One(None),
    )


def _client(db: Any, role: str = "member") -> TestClient:
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
    app.dependency_overrides[admin_mod.get_db] = fake_db
    app.dependency_overrides[builder_mod.get_current_role] = fake_role
    app.dependency_overrides[builder_mod.get_current_user_id] = fake_user
    app.dependency_overrides[admin_mod.ensure_admin] = fake_admin
    app.dependency_overrides[builder_mod.get_rate_limiter] = lambda: _Limiter()
    return TestClient(app)


def test_member_sees_default_plus_admin_allowed_models() -> None:
    db = _db({"builder_allowed_models": '["openai/gpt-5"]'})

    body = _client(db).get("/builder/models").json()

    assert body["default"] == "anthropic/claude-sonnet-4-5"
    assert [m["id"] for m in body["models"]] == ["anthropic/claude-sonnet-4-5", "openai/gpt-5"]
    assert body["models"][1]["label"] == "GPT-5"


def test_message_with_a_model_the_admin_did_not_allow_is_rejected() -> None:
    resp = _client(_db({})).post(
        "/builder/w1/message", json={"message": "hi", "model": "groq/llama"}
    )

    assert resp.status_code == 400
    assert "groq/llama" in resp.json()["detail"]


def test_chosen_allowed_model_is_the_one_built(monkeypatch: Any) -> None:
    built: list[str] = []

    def fake_build(model_string: str) -> object:
        built.append(model_string)
        raise RuntimeError("stop after model selection")

    monkeypatch.setattr(builder_mod, "build_chat_model", fake_build)
    client = TestClient(
        _client(_db({"builder_allowed_models": '["openai/gpt-5"]'})).app,
        raise_server_exceptions=False,
    )

    client.post("/builder/w1/message", json={"message": "hi", "model": "openai/gpt-5"})

    assert built == ["openai/gpt-5"]


def test_admin_can_read_and_update_the_allowed_models() -> None:
    db = _db({})
    client = _client(db, role="admin")

    put = client.put(
        "/admin/builder-models",
        json={"defaultModel": "anthropic/claude-sonnet-4-5", "allowedModels": ["openai/gpt-5"]},
    )

    assert put.status_code == 200
    assert put.json() == {
        "defaultModel": "anthropic/claude-sonnet-4-5",
        "allowedModels": ["anthropic/claude-sonnet-4-5", "openai/gpt-5"],
    }
    assert client.get("/admin/builder-models").json() == put.json()


def test_admin_cannot_allow_a_model_missing_from_the_catalog() -> None:
    resp = _client(_db({}), role="admin").put(
        "/admin/builder-models",
        json={"defaultModel": "anthropic/claude-sonnet-4-5", "allowedModels": ["x/nope"]},
    )

    assert resp.status_code == 400
    assert "x/nope" in resp.json()["detail"]


def test_members_cannot_change_the_allowed_models() -> None:
    resp = _client(_db({}), role="member").put(
        "/admin/builder-models",
        json={"defaultModel": "anthropic/claude-sonnet-4-5", "allowedModels": []},
    )

    assert resp.status_code == 403
