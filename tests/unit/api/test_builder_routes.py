from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import builder as builder_mod
from src.api.builder import _persist, router  # pyright: ignore[reportPrivateUsage]
from src.db.repos import workflows as workflows_repo
from src.engine.workflow import JIRA_TOKEN_REDACTED, Workflow
from tests.unit.db_fakes import (
    FakeSessionmaker,
    RecordingSessionmaker,
    assert_no_network_inside_session,
    make_workflow,
    override_session,
    patch_workflow_repos,
)


class _Limiter:
    async def check(self, route_key: str, client_key: str, config: Any) -> tuple[bool, float]:
        return True, 0.0


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    workflow: Any = None,
    role: tuple[str, str] = ("dev", "member"),
    sessionmaker: Any = None,
    assigned: bool = False,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def fake_db() -> Any:
        return None

    async def fake_role() -> tuple[str, str]:
        return role

    app.state.builder_sessions = {}
    app.state.db_sessionmaker = sessionmaker if sessionmaker is not None else FakeSessionmaker()
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = workflow
    repos.exists.return_value = assigned
    app.dependency_overrides[builder_mod.get_db] = fake_db
    override_session(app)  # the route resolves its SQLAlchemy session up front
    app.dependency_overrides[builder_mod.get_current_role] = fake_role
    app.dependency_overrides[builder_mod.get_rate_limiter] = lambda: _Limiter()
    return TestClient(app)


def test_session_missing_returns_session_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.post("/builder/w1/message", json={"message": "hi", "sessionId": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sessionId"] == "nope"
    assert any(e.get("type") == "session_expired" for e in body["events"])


def test_non_owner_member_gets_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, make_workflow(user_id="other-owner"), role=("me", "member"))
    resp = client.post("/builder/w1/message", json={"message": "hi"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Workflow not found."


def test_missing_workflow_gets_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, None)
    resp = client.post("/builder/w1/message", json={"message": "hi"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Workflow 'w1' not found."


def test_assignee_passes_the_access_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The assignment check runs in the same session as the read; reaching the policy
    load (stubbed to a sentinel status) proves access was granted."""

    async def _policy(_session: Any) -> Any:
        raise HTTPException(status_code=418, detail="reached the policy load")

    monkeypatch.setattr(builder_mod, "load_policy", _policy)
    client = _make_client(
        monkeypatch, make_workflow(user_id="other-owner"), role=("me", "member"), assigned=True
    )
    resp = client.post("/builder/w1/message", json={"message": "hi"})
    assert resp.status_code == 418


def test_workflow_session_is_closed_before_the_llm_turn_and_persist_uses_a_fresh_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    wf = make_workflow(
        user_id="dev",
        nodes=[
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        edges=[{"id": "e1", "source": "s", "target": "e"}],
    )
    client = _make_client(monkeypatch, wf, sessionmaker=RecordingSessionmaker(events))

    async def _get(*_a: object, **_k: object) -> Any:
        events.append("read")
        return wf

    async def _update(*_a: object, **_k: object) -> Any:
        events.append("write")
        return wf

    class _Policy:
        options: ClassVar[list[Any]] = []
        default = "x/y"

        def resolve(self, _model: str | None) -> str:
            return "x/y"

    async def _policy(_session: Any) -> Any:
        return _Policy()

    async def _catalog(_session: Any) -> list[Any]:
        return []

    async def _turn(**kwargs: Any) -> Any:
        events.append("net:llm_turn")
        return [], "ok", kwargs["workflow"]

    monkeypatch.setattr(workflows_repo, "get", _get)
    monkeypatch.setattr(workflows_repo, "update", _update)
    monkeypatch.setattr(builder_mod, "load_policy", _policy)
    monkeypatch.setattr(builder_mod, "enabled_model_catalog", _catalog)

    def _build(_model: str) -> object:
        return object()

    monkeypatch.setattr(builder_mod, "build_chat_model", _build)
    monkeypatch.setattr(builder_mod, "run_builder_turn", _turn)

    resp = client.post("/builder/w1/message", json={"message": "hi"})

    assert resp.status_code == 200, resp.text
    assert events == [
        "session:enter",
        "read",
        "session:exit",
        "net:llm_turn",
        "session:enter",
        "write",
        "session:exit",
    ]
    assert_no_network_inside_session(events)


def test_builder_without_a_database_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, make_workflow(user_id="dev"))
    client.app.state.db_sessionmaker = None  # type: ignore[attr-defined]
    resp = client.post("/builder/w1/message", json={"message": "hi"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "database unavailable"


def _engine_workflow(**node_data: Any) -> Workflow:
    nodes: list[dict[str, Any]] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ]
    if node_data:
        nodes.insert(
            1, {"id": "j1", "type": "jira", "position": {"x": 50, "y": 0}, "data": node_data}
        )
    return Workflow.model_validate(
        {
            "id": "w1",
            "name": "Sample",
            "description": None,
            "nodes": nodes,
            "edges": [{"id": "e1", "source": "s", "target": "e"}],
        }
    )


async def test_persist_updates_only_nodes_and_edges_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessionmaker = FakeSessionmaker()
    update = AsyncMock(return_value=make_workflow())
    monkeypatch.setattr(workflows_repo, "update", update)

    row = await _persist(sessionmaker, "w1", _engine_workflow(), make_workflow())  # type: ignore[arg-type]

    assert row.id == "w1"
    assert update.await_args is not None
    assert update.await_args.args[1] == "w1"
    assert set(update.await_args.args[2]) == {"nodes", "edges"}
    sessionmaker.session.commit.assert_awaited_once()


async def test_persist_when_the_workflow_vanished_is_404_and_commits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessionmaker = FakeSessionmaker()
    monkeypatch.setattr(workflows_repo, "update", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as info:
        await _persist(sessionmaker, "w1", _engine_workflow(), make_workflow())  # type: ignore[arg-type]

    assert info.value.status_code == 404
    sessionmaker.session.commit.assert_not_awaited()


_JIRA = {"label": "Jira", "domain": "t.atlassian.net", "email": "t@example.com"}


async def test_persist_keeps_the_stored_token_when_the_client_echoes_the_redacted_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = AsyncMock(return_value=make_workflow())
    monkeypatch.setattr(workflows_repo, "update", update)
    existing = make_workflow(
        nodes=[{"id": "j1", "type": "jira", "data": {**_JIRA, "apiToken": "stored-ciphertext"}}]
    )

    await _persist(
        FakeSessionmaker(),  # type: ignore[arg-type]
        "w1",
        _engine_workflow(**_JIRA, apiToken=JIRA_TOKEN_REDACTED),
        existing,
    )

    saved = update.await_args.args[2]["nodes"]  # type: ignore[union-attr]
    (jira,) = [n for n in saved if n["id"] == "j1"]
    assert jira["data"]["apiToken"] == "stored-ciphertext"


async def test_persist_stores_none_for_a_redacted_echo_of_an_unknown_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = AsyncMock(return_value=make_workflow())
    monkeypatch.setattr(workflows_repo, "update", update)

    await _persist(
        FakeSessionmaker(),  # type: ignore[arg-type]
        "w1",
        _engine_workflow(**_JIRA, apiToken=JIRA_TOKEN_REDACTED),
        make_workflow(nodes=[]),
    )

    saved = update.await_args.args[2]["nodes"]  # type: ignore[union-attr]
    (jira,) = [n for n in saved if n["id"] == "j1"]
    assert jira["data"]["apiToken"] is None
