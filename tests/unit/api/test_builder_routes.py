from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import builder as builder_mod
from src.api.builder import _persist, router  # pyright: ignore[reportPrivateUsage]
from src.engine.workflow import Workflow


class _Limiter:
    async def check(self, route_key: str, client_key: str, config: Any) -> tuple[bool, float]:
        return True, 0.0


class _FindUniqueRepo:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def find_unique(self, **kwargs: Any) -> Any:
        return self._row


class _FakeDb:
    def __init__(self, workflow_row: Any, assignment_row: Any = None) -> None:
        self.workflow = _FindUniqueRepo(workflow_row)
        self.workflowassignment = _FindUniqueRepo(assignment_row)


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "dev",
        "name": "Sample",
        "description": None,
        "nodes": [],
        "edges": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_client(db: Any = None, role: tuple[str, str] = ("dev", "member")) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def fake_db() -> Any:
        return db

    async def fake_role() -> tuple[str, str]:
        return role

    app.state.builder_sessions = {}
    app.dependency_overrides[builder_mod.get_db] = fake_db
    app.dependency_overrides[builder_mod.get_current_role] = fake_role
    app.dependency_overrides[builder_mod.get_rate_limiter] = lambda: _Limiter()
    return TestClient(app)


def test_session_missing_returns_session_expired() -> None:
    client = _make_client()
    resp = client.post("/builder/w1/message", json={"message": "hi", "sessionId": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sessionId"] == "nope"
    assert any(e.get("type") == "session_expired" for e in body["events"])


def test_non_owner_member_gets_404() -> None:
    db = _FakeDb(workflow_row=_wf_row(userId="other-owner"))
    client = _make_client(db=db, role=("me", "member"))
    resp = client.post("/builder/w1/message", json={"message": "hi"})
    assert resp.status_code == 404


async def test_persist_updates_only_nodes_and_edges() -> None:
    captured: dict[str, Any] = {}

    class _UpdateRepo:
        async def update(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"id": "w1"}

    db: Any = SimpleNamespace(workflow=_UpdateRepo())
    workflow = Workflow.model_validate(
        {
            "id": "w1",
            "name": "Sample",
            "description": None,
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
                {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
            ],
            "edges": [{"id": "e1", "source": "s", "target": "e"}],
        }
    )
    existing = SimpleNamespace(nodes=[], edges=[])

    await _persist(db, "w1", workflow, existing)

    assert captured["where"] == {"id": "w1"}
    assert set(captured["data"].keys()) == {"nodes", "edges"}
