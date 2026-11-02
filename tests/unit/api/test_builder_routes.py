from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import builder as builder_mod
from src.api.builder import router


class _Limiter:
    async def check(self, route_key: str, client_key: str, config: Any) -> tuple[bool, float]:
        return True, 0.0


def _make_client():
    app = FastAPI()
    app.include_router(router)

    async def fake_db():
        return None

    async def fake_role():
        return ("dev", "member")

    app.state.builder_sessions = {}
    app.dependency_overrides[builder_mod.get_db] = fake_db
    app.dependency_overrides[builder_mod.get_current_role] = fake_role
    app.dependency_overrides[builder_mod.get_rate_limiter] = lambda: _Limiter()
    return TestClient(app)


def test_session_missing_returns_session_expired():
    client = _make_client()
    resp = client.post("/builder/w1/message", json={"message": "hi", "sessionId": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sessionId"] == "nope"
    assert any(e.get("type") == "session_expired" for e in body["events"])
