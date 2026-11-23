"""Tests for workflow assignment CRUD + authz relax (Account + Workflow Sharing plan, Part B)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.errors import AlreadyExists, ReferencedRowMissing
from src.db.repos import users as users_repo
from src.db.repos import workflow_assignments as assignments_repo
from src.db.repos.workflow_assignments import AssignmentRecord
from src.main import create_app
from src.security.auth import get_current_role
from src.security.jwt import create_access_token
from tests.unit.db_fakes import (
    FakeSessionmaker,
    make_user,
    make_workflow,
    override_session,
    patch_workflow_repos,
)


def _assignment(**overrides: Any) -> AssignmentRecord:
    base: dict[str, Any] = {
        "id": "a1",
        "workflow_id": "w1",
        "user_id": "assignee1",
        "assigned_by_id": "owner1",
        "assigned_at": datetime(2026, 7, 9, tzinfo=UTC),
    }
    base.update(overrides)
    return AssignmentRecord(**base)


def _repos(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch the assignment repo functions the routes call (the workflow ones via db_fakes)."""
    wf = patch_workflow_repos(monkeypatch)
    wf.get.return_value = make_workflow(id="w1", user_id="owner1")
    create = AsyncMock(return_value=_assignment())
    delete = AsyncMock(return_value=True)
    listing = AsyncMock(return_value=[_assignment()])
    monkeypatch.setattr(assignments_repo, "create", create)
    monkeypatch.setattr(assignments_repo, "delete", delete)
    monkeypatch.setattr(assignments_repo, "list_for_workflow", listing)
    return SimpleNamespace(wf=wf, create=create, delete=delete, list=listing)


async def _known_users(_session: object, user_id: str) -> object | None:
    return None if user_id == "ghost" else make_user(id=user_id)


def _as_admin(client: TestClient) -> None:
    client.app.dependency_overrides[get_current_role] = lambda: ("admin1", "admin")  # type: ignore[attr-defined]


def _commit(client: TestClient) -> AsyncMock:
    return client.app.state.session.commit  # type: ignore[attr-defined]


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.session = override_session(app)
    app.state.db_sessionmaker = (
        FakeSessionmaker()
    )  # production fails closed without a live user check
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="assignee1")))
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app)


def test_owner_can_grant_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["userId"] == "assignee1"
    repos.create.assert_awaited_once()
    assert repos.create.await_args.kwargs == {
        "workflow_id": "w1",
        "user_id": "assignee1",
        "assigned_by_id": "owner1",
    }
    _commit(client).assert_awaited_once()


def test_grant_writes_before_commit_and_locks_the_workflow_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    order: list[str] = []

    async def _create(*_a: object, **_k: object) -> AssignmentRecord:
        order.append("create")
        return _assignment()

    async def _commit_side_effect() -> None:
        order.append("commit")

    repos.create.side_effect = _create
    _commit(client).side_effect = _commit_side_effect
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201, resp.text
    assert order == ["create", "commit"]
    assert repos.wf.get.await_args.kwargs == {"for_update": True}  # type: ignore[union-attr]


def test_grant_when_workflow_vanishes_concurrently_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    repos.create.side_effect = ReferencedRowMissing("workflow_assignments_workflow_id_fkey")
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Workflow 'w1' not found."
    _commit(client).assert_not_awaited()


@pytest.mark.parametrize(
    "constraint", ["workflow_assignments_user_id_fkey", "workflow_assignments_assigned_by_id_fkey"]
)
def test_grant_when_a_user_vanishes_concurrently_is_404_naming_the_user(
    monkeypatch: pytest.MonkeyPatch, constraint: str
) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    repos.create.side_effect = ReferencedRowMissing(constraint)
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "user 'assignee1' not found"
    _commit(client).assert_not_awaited()


def test_non_owner_non_admin_cannot_grant_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    token = create_access_token("stranger1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    repos.create.assert_not_awaited()


def test_grant_to_unknown_user_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(side_effect=_known_users))
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/ghost", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "user 'ghost' not found"
    repos.create.assert_not_awaited()


def test_owner_can_revoke_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    token = create_access_token("owner1")
    resp = client.delete(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 204, resp.text
    repos.delete.assert_awaited_once()
    assert repos.delete.await_args.args[1:] == ("w1", "assignee1")
    assert repos.wf.get.await_args.kwargs == {"for_update": True}  # type: ignore[union-attr]
    _commit(client).assert_awaited_once()


def test_grant_duplicate_assignment_returns_409_without_sql_in_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    repos.create.side_effect = AlreadyExists("workflow_assignments_workflow_id_user_id_key")
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == (
        "assignment already exists for user 'assignee1': "
        "unique constraint violated: workflow_assignments_workflow_id_user_id_key"
    )
    _commit(client).assert_not_awaited()


def test_revoke_nonexistent_assignment_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    repos.delete.return_value = False
    token = create_access_token("owner1")
    resp = client.delete(
        "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "assignment not found for user 'assignee1'"
    _commit(client).assert_not_awaited()


def test_list_assignments(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    _repos(monkeypatch)
    token = create_access_token("owner1")
    resp = client.get("/workflows/w1/assignments", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["userId"] == "assignee1"
    assert body[0]["workflowId"] == "w1" and body[0]["assignedById"] == "owner1"


def test_assign_owner_by_id_is_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    repos.wf.update.return_value = make_workflow(id="w1", user_id="assignee1")
    token = create_access_token("admin1")
    _as_admin(client)
    resp = client.patch(
        "/workflows/w1/owner",
        json={"userId": "assignee1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert repos.wf.update.await_args.args[1:] == ("w1", {"user_id": "assignee1"})
    _commit(client).assert_awaited_once()
    client.app.state.session.rollback.assert_not_awaited()  # type: ignore[attr-defined]


def test_assign_owner_by_email_lowercases(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    by_email = AsyncMock(return_value=make_user(id="u9"))
    monkeypatch.setattr(users_repo, "get_by_email", by_email)
    repos.wf.update.return_value = make_workflow(id="w1", user_id="u9")
    token = create_access_token("admin1")
    _as_admin(client)
    resp = client.patch(
        "/workflows/w1/owner",
        json={"email": "Bob@X.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert by_email.await_args is not None
    assert by_email.await_args.args[1] == "bob@x.com"
    assert repos.wf.update.await_args.args[2] == {"user_id": "u9"}
    _commit(client).assert_awaited_once()


def test_assign_owner_unknown_user_404_commits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(side_effect=_known_users))
    token = create_access_token("admin1")
    _as_admin(client)
    resp = client.patch(
        "/workflows/w1/owner",
        json={"userId": "ghost"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "user 'ghost' not found"
    repos.wf.update.assert_not_awaited()
    _commit(client).assert_not_awaited()


def test_assign_owner_failure_after_lookup_commits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_client(monkeypatch).app, raise_server_exceptions=True)
    repos = _repos(monkeypatch)
    repos.wf.update.side_effect = RuntimeError("db down")
    token = create_access_token("admin1")
    _as_admin(client)
    with pytest.raises(RuntimeError):
        client.patch(
            "/workflows/w1/owner",
            json={"userId": "assignee1"},
            headers={"Authorization": f"Bearer {token}"},
        )
    _commit(client).assert_not_awaited()


def test_grant_failure_after_lookup_commits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = _repos(monkeypatch)
    repos.create.side_effect = RuntimeError("db down")
    token = create_access_token("owner1")
    with pytest.raises(RuntimeError):
        client.post(
            "/workflows/w1/assignments/assignee1", headers={"Authorization": f"Bearer {token}"}
        )
    _commit(client).assert_not_awaited()


def test_assignee_can_read_private_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = make_workflow(user_id="owner1")
    repos.exists.return_value = True
    token = create_access_token("assignee1")
    resp = client.get("/workflows/w1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


def test_non_assignee_still_gets_404_on_private_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = make_workflow(user_id="owner1")
    repos.exists.return_value = False
    token = create_access_token("stranger1")
    resp = client.get("/workflows/w1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_assignee_can_update_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = make_workflow(user_id="owner1")
    repos.exists.return_value = True
    repos.update.return_value = make_workflow(user_id="owner1", name="Renamed")
    token = create_access_token("assignee1")
    body = {
        "name": "Renamed",
        "nodes": [
            {
                "id": "s",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {"label": "S"},
            },
            {
                "id": "e",
                "type": "end",
                "position": {"x": 100, "y": 0},
                "data": {"label": "E"},
            },
        ],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    resp = client.put("/workflows/w1", json=body, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    repos.exists.assert_awaited_once()
    repos.update.assert_awaited_once()
