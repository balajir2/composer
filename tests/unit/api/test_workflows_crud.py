"""Tests for workflow list + search endpoints (Phase 7b)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _wf_row_create(**overrides: Any) -> SimpleNamespace:
    """Minimal workflow row for POST /workflows create tests."""
    base: dict[str, Any] = {
        "id": "w-new",
        "userId": "dev",
        "name": "Created",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [],
        "edges": [],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-04-22T00:00:00Z",
        "updatedAt": "2026-04-22T00:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _wf_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "w1",
        "userId": "user-42",
        "name": "Sample",
        "description": "A test workflow",
        "category": "utilities",
        "tags": ["x"],
        "difficulty": None,
        "estimatedTime": None,
        "nodes": [],
        "edges": [],
        "version": None,
        "isTemplate": False,
        "isPublic": False,
        "createdAt": "2026-04-21T00:00:00Z",
        "updatedAt": "2026-04-21T01:00:00Z",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(
    monkeypatch: pytest.MonkeyPatch, rows: list[Any], total: int
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.count = AsyncMock(return_value=total)
    db.workflow.find_many = AsyncMock(return_value=rows)
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_list_workflows_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [_wf_row(), _wf_row(id="w2", name="Another")], total=2)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_workflows_filters_by_is_template(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_wf_row(isTemplate=True)], total=1)
    resp = client.get("/workflows?isTemplate=true")
    assert resp.status_code == 200
    # With authz, where is {"AND": [authz_or, {"isTemplate": True}]}
    where = db.workflow.find_many.await_args.kwargs["where"]
    and_clauses = where.get("AND", [])
    assert any(c.get("isTemplate") is True for c in and_clauses)


def test_list_workflows_mine_filters_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_wf_row(userId="dev")], total=1)
    resp = client.get("/workflows?mine=true")
    assert resp.status_code == 200
    # mine=true → authz_where = {"userId": user_id} (no OR wrapper)
    # dev-mode fallback (ADR-0015) sets user_id='dev'
    assert db.workflow.find_many.await_args.kwargs["where"].get("userId") == "dev"


def test_list_workflows_limit_over_100_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows?limit=150")
    assert resp.status_code == 422


def test_search_workflows_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client(monkeypatch, [_wf_row(name="SearchTest")], total=1)
    resp = client.get("/workflows/search?q=search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "SearchTest"
    where = db.workflow.find_many.await_args.kwargs["where"]
    # With authz: where = {"AND": [authz_or, text_match_or]}
    assert "AND" in where
    where_str = str(where)
    assert "OR" in where_str


def test_search_workflows_empty_q_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows/search?q=")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 2 — GET /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def _client_fetch(monkeypatch: pytest.MonkeyPatch, row: Any | None) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=row)
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_get_workflow_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _wf_row(id="wx", name="Hello", userId="dev")  # match dev-mode caller
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Hello"


def test_get_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_fetch(monkeypatch, None)
    resp = client.get("/workflows/ghost")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 3 — PUT /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def _client_put(
    monkeypatch: pytest.MonkeyPatch,
    existing: Any | None,
    updated: Any | None,
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.update = AsyncMock(return_value=updated)
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


_VALID_BODY: dict[str, Any] = {
    "name": "Updated",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


def test_put_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="dev")  # dev-mode fallback user
    updated = _wf_row(id="w1", userId="dev", name="Updated")
    client, _ = _client_put(monkeypatch, existing, updated)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Updated"


def test_put_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="someone-else")
    client, _ = _client_put(monkeypatch, existing, None)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 403


def test_put_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_put(monkeypatch, None, None)
    resp = client.put("/workflows/ghost", json=_VALID_BODY)
    assert resp.status_code == 404


def test_put_workflow_invalid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """No start node → validator raises → 422."""
    existing = _wf_row(id="w1", userId="dev")
    client, _ = _client_put(monkeypatch, existing, None)
    bad_body: dict[str, Any] = {
        "name": "Bad",
        "nodes": [
            {"id": "e", "type": "end", "position": {"x": 0, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [],
    }
    resp = client.put("/workflows/w1", json=bad_body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 4 — DELETE /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def _client_delete(
    monkeypatch: pytest.MonkeyPatch, existing: Any | None
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=existing)
    db.workflow.delete = AsyncMock()
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_delete_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="dev")
    client, db = _client_delete(monkeypatch, existing)
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 204
    db.workflow.delete.assert_awaited_once()


def test_delete_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", userId="other")
    client, _ = _client_delete(monkeypatch, existing)
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 403


def test_delete_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_delete(monkeypatch, None)
    resp = client.delete("/workflows/ghost")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 8 — workflow read authz (ADR-0021)
# ---------------------------------------------------------------------------


def test_get_workflow_private_not_owner_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-owner reading a private workflow gets 404 (info-leak tight)."""
    row = _wf_row(id="wx", userId="someone-else", isPublic=False)
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 404


def test_get_workflow_public_non_owner_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public workflows are readable by non-owners."""
    row = _wf_row(id="wx", userId="other", isPublic=True, name="Public")
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Public"


def test_list_workflows_where_clause_has_authz_or(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List query must OR(isPublic=true, userId=caller)."""
    client, db = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    where = db.workflow.find_many.await_args.kwargs["where"]
    # Inspect: the authz OR should be nested somewhere
    where_str = str(where)
    assert "isPublic" in where_str
    assert "OR" in where_str


# ---------------------------------------------------------------------------
# Phase 8 Task 3 — workflow size caps (ADR-0022)
# ---------------------------------------------------------------------------


def test_create_workflow_over_node_limit_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """101 nodes → 422."""
    # Build an over-limit workflow: 1 start + 100 set-state + 1 end = 102 nodes
    nodes: list[dict[str, Any]] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
    ]
    for i in range(100):
        nodes.append(
            {
                "id": f"n{i}",
                "type": "set-state",
                "position": {"x": i + 1, "y": 0},
                "data": {"label": f"N{i}", "stateKey": "k", "stateValue": "v"},
            }
        )
    nodes.append({"id": "e", "type": "end", "position": {"x": 999, "y": 0}, "data": {"label": "E"}})
    edges: list[dict[str, Any]] = []
    for i in range(101):
        source = "s" if i == 0 else f"n{i - 1}"
        target = f"n{i}" if i < 100 else "e"
        edges.append({"id": f"e{i}", "source": source, "target": target})

    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()

    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.create = AsyncMock(return_value=_wf_row_create(id="w-big", userId="dev"))
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    client = TestClient(app)

    body: dict[str, Any] = {"name": "Too big", "nodes": nodes, "edges": edges}
    resp = client.post("/workflows", json=body)
    assert resp.status_code == 422
    assert "max_nodes" in resp.json()["detail"]


def test_create_workflow_at_node_limit_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly 100 nodes is allowed (boundary inclusive)."""
    nodes: list[dict[str, Any]] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
    ]
    for i in range(98):
        nodes.append(
            {
                "id": f"n{i}",
                "type": "set-state",
                "position": {"x": i + 1, "y": 0},
                "data": {"label": f"N{i}", "stateKey": "k", "stateValue": "v"},
            }
        )
    nodes.append({"id": "e", "type": "end", "position": {"x": 999, "y": 0}, "data": {"label": "E"}})
    assert len(nodes) == 100

    edges: list[dict[str, Any]] = []
    for i in range(99):
        source = "s" if i == 0 else f"n{i - 1}"
        target = f"n{i}" if i < 98 else "e"
        edges.append({"id": f"e{i}", "source": source, "target": target})

    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()

    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.create = AsyncMock(return_value=_wf_row_create(id="w-big", userId="dev"))
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    client = TestClient(app)

    body: dict[str, Any] = {"name": "Big", "nodes": nodes, "edges": edges}
    resp = client.post("/workflows", json=body)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Phase 9 Task 5 — Admin role bypass (ADR-0025)
# ---------------------------------------------------------------------------


def test_admin_can_read_other_users_private_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin bypass: 200 on another user's private workflow."""
    from src.security.auth import get_current_role

    row = _wf_row(id="wx", userId="someone-else", isPublic=False, name="Private")
    client, _ = _client_fetch(monkeypatch, row)
    # Override the dependency on the app instance directly
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.get("/workflows/wx")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert resp.json()["name"] == "Private"


def test_admin_can_put_other_users_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin can PUT another user's workflow."""
    from src.security.auth import get_current_role

    existing = _wf_row(id="w1", userId="someone-else")
    updated = _wf_row(id="w1", userId="someone-else", name="Updated")
    client, _ = _client_put(monkeypatch, existing, updated)
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.put("/workflows/w1", json=_VALID_BODY)
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated"


def test_admin_cannot_delete_other_users_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin bypass does NOT apply to DELETE per spec §5.1."""
    existing = _wf_row(id="w1", userId="someone-else")
    client, _ = _client_delete(monkeypatch, existing)
    # No admin bypass — DELETE stays strict owner-only
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 403


def _client_patch_owner(
    monkeypatch: pytest.MonkeyPatch,
    wf_row: Any | None,
    target_user: Any | None,
    updated_row: Any | None,
) -> tuple[TestClient, MagicMock]:
    """Helper for PATCH /workflows/{id}/owner tests (admin-only endpoint)."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=wf_row)
    db.workflow.update = AsyncMock(return_value=updated_row)
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=target_user)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), db


def test_patch_workflow_owner_by_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin PATCHes ownership to a user identified by userId."""
    from src.security.auth import ensure_admin

    target = SimpleNamespace(id="new-owner", email="target@x.com")
    wf = _wf_row(id="wx", userId="old-owner")
    updated = _wf_row(id="wx", userId="new-owner")
    client, db = _client_patch_owner(monkeypatch, wf, target, updated)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/workflows/wx/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    db.workflow.update.assert_awaited_once()
    call_data = db.workflow.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert call_data["userId"] == "new-owner"


def test_patch_workflow_owner_by_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin PATCHes ownership to a user identified by email."""
    from src.security.auth import ensure_admin

    target = SimpleNamespace(id="uid-email", email="target@x.com")
    wf = _wf_row(id="wx", userId="old-owner")
    updated = _wf_row(id="wx", userId="uid-email")
    client, db = _client_patch_owner(monkeypatch, wf, target, updated)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/workflows/wx/owner", json={"email": "target@x.com"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    db.workflow.update.assert_awaited_once()
    call_data = db.workflow.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert call_data["userId"] == "uid-email"


def test_patch_workflow_owner_unknown_email_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH with unknown email → 404."""
    from src.security.auth import ensure_admin

    wf = _wf_row(id="wx", userId="old-owner")
    client, _ = _client_patch_owner(monkeypatch, wf, None, None)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/workflows/wx/owner", json={"email": "ghost@x.com"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 404


def test_patch_workflow_owner_unknown_workflow_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH with unknown workflow → 404."""
    from src.security.auth import ensure_admin

    target = SimpleNamespace(id="new-owner", email="target@x.com")
    client, _ = _client_patch_owner(monkeypatch, None, target, None)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/workflows/ghost/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 404


def test_patch_workflow_owner_member_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin calling PATCH /owner → 403.

    In test mode the db is a MagicMock (not Prisma), so get_current_role
    returns ('dev', 'member') and ensure_admin raises 403.
    """
    wf = _wf_row(id="wx", userId="old-owner")
    target = SimpleNamespace(id="new-owner", email="t@x.com")
    client, _ = _client_patch_owner(monkeypatch, wf, target, None)
    # No admin override → dev-mode 'dev' user → member → ensure_admin raises 403
    resp = client.patch("/workflows/wx/owner", json={"userId": "new-owner"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Phase 10a Task 3 — workflow publish / unpublish (isProduction + externalSlug)
# ---------------------------------------------------------------------------

_PUBLISH_BODY: dict[str, Any] = {
    **_VALID_BODY,
    "isProduction": True,
    "externalSlug": "my-workflow",
}


def test_put_publish_requires_external_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting isProduction=True without externalSlug → 422."""
    existing = _wf_row(id="w1", userId="dev")
    client, _ = _client_put(monkeypatch, existing, None)
    body: dict[str, Any] = {**_VALID_BODY, "isProduction": True}
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 422
    assert "external_slug is required" in resp.json()["detail"]


def test_put_publish_invalid_slug_format_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid slug (uppercase, starts with hyphen) → 422."""
    existing = _wf_row(id="w1", userId="dev")
    client, _ = _client_put(monkeypatch, existing, None)
    for bad_slug in ("-bad-slug", "UPPERCASE", "has space", "a"):
        body: dict[str, Any] = {**_VALID_BODY, "isProduction": True, "externalSlug": bad_slug}
        resp = client.put("/workflows/w1", json=body)
        assert resp.status_code == 422, (
            f"expected 422 for slug {bad_slug!r}, got {resp.status_code}"
        )


def test_put_publish_sets_isproduction_and_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid publish request updates both isProduction and externalSlug."""
    existing = _wf_row(id="w1", userId="dev")
    updated = _wf_row(
        id="w1",
        userId="dev",
        name="Updated",
        isProduction=True,
        externalSlug="my-workflow",
    )
    client, db = _client_put(monkeypatch, existing, updated)
    resp = client.put("/workflows/w1", json=_PUBLISH_BODY)
    assert resp.status_code == 200, resp.text
    call_data = db.workflow.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert call_data["isProduction"] is True
    assert call_data["externalSlug"] == "my-workflow"


def test_put_unpublish_clears_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """isProduction=False clears externalSlug (sets to None)."""
    existing = _wf_row(id="w1", userId="dev", isProduction=True, externalSlug="my-workflow")
    updated = _wf_row(id="w1", userId="dev", name="Updated", isProduction=False, externalSlug=None)
    client, db = _client_put(monkeypatch, existing, updated)
    body: dict[str, Any] = {**_VALID_BODY, "isProduction": False}
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text
    call_data = db.workflow.update.await_args.kwargs["data"]  # type: ignore[union-attr]
    assert call_data["isProduction"] is False
    assert call_data["externalSlug"] is None


def test_put_slug_conflict_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prisma UniqueViolationError on slug collision → 409."""
    from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]

    existing = _wf_row(id="w1", userId="dev")
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=existing)
    # Simulate a Prisma unique violation on update
    db.workflow.update = AsyncMock(
        side_effect=UniqueViolationError(
            {"user_facing_error": {"message": "Unique constraint failed"}}
        )
    )
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    client = TestClient(app)

    resp = client.put("/workflows/w1", json=_PUBLISH_BODY)
    assert resp.status_code == 409
    assert "external_slug already in use" in resp.json()["detail"]
