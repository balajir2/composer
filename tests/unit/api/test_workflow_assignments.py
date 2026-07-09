"""Tests for workflow assignment CRUD + authz relax (Account + Workflow Sharing plan, Part B)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.jwt import create_access_token


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "owner1",
        "name": "Shared workflow",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [{"id": "s", "type": "start"}],
        "edges": [],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "isProduction": False,
        "externalSlug": None,
        "createdAt": "2026-07-09T00:00:00Z",
        "updatedAt": "2026-07-09T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _assignment_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "a1",
        "workflowId": "w1",
        "userId": "assignee1",
        "assignedById": "owner1",
        "assignedAt": "2026-07-09T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflowassignment = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="assignee1", role="member", isActive=True)
    )
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_owner_can_grant_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=None)
    db.workflowassignment.create = AsyncMock(return_value=_assignment_row())
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    db.workflowassignment.create.assert_awaited_once()


def test_non_owner_non_admin_cannot_grant_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    token = create_access_token("stranger1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_owner_can_revoke_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.delete = AsyncMock(return_value=_assignment_row())
    token = create_access_token("owner1")
    resp = client.delete(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204, resp.text


def test_grant_duplicate_assignment_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prisma UniqueViolationError on double-grant → 409."""
    from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.create = AsyncMock(
        side_effect=UniqueViolationError({"user_facing_error": {"meta": {}}})
    )
    token = create_access_token("owner1")
    resp = client.post(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text


def test_revoke_nonexistent_assignment_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prisma RecordNotFoundError on double-revoke → 404."""
    from prisma.errors import RecordNotFoundError  # pyright: ignore[reportMissingImports]

    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.delete = AsyncMock(
        side_effect=RecordNotFoundError({"user_facing_error": {"meta": {}}})
    )
    token = create_access_token("owner1")
    resp = client.delete(
        "/workflows/w1/assignments/assignee1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


def test_list_assignments(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_many = AsyncMock(return_value=[_assignment_row()])
    token = create_access_token("owner1")
    resp = client.get(
        "/workflows/w1/assignments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["userId"] == "assignee1"


def test_assignee_can_read_private_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=_assignment_row())
    token = create_access_token("assignee1")
    resp = client.get("/workflows/w1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


def test_non_assignee_still_gets_404_on_private_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=None)
    token = create_access_token("stranger1")
    resp = client.get("/workflows/w1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_assignee_can_update_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch)
    db.workflow.find_unique = AsyncMock(return_value=_wf_row())
    db.workflowassignment.find_unique = AsyncMock(return_value=_assignment_row())
    db.workflow.update = AsyncMock(return_value=_wf_row(name="Renamed"))
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
