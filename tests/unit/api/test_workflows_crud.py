"""Tests for workflow list + search endpoints (Phase 7b)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.errors import AlreadyExists, StalePrecondition
from src.db.repos import users as users_repo
from src.db.repos.workflows import WorkflowRecord
from src.main import create_app
from tests.unit.db_fakes import (
    WorkflowRepoMocks,
    make_user,
    make_workflow,
    override_session,
    patch_workflow_repos,
)


def _wf_row_create(**overrides: Any) -> WorkflowRecord:
    """Minimal workflow record for POST /workflows create tests."""
    base: dict[str, Any] = {"id": "w-new", "user_id": "dev", "name": "Created"}
    base.update(overrides)
    return make_workflow(**base)


def _wf_row(**overrides: Any) -> WorkflowRecord:
    base: dict[str, Any] = {
        "id": "w1",
        "user_id": "user-42",
        "name": "Sample",
        "description": "A test workflow",
        "category": "utilities",
        "tags": ["x"],
        "updated_at": "2026-04-21T01:00:00Z",
    }
    base.update(overrides)
    return make_workflow(**base)


def _client(
    monkeypatch: pytest.MonkeyPatch, rows: list[Any], total: int
) -> tuple[TestClient, WorkflowRepoMocks]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.list_page.return_value = (rows, total)
    repos.search.return_value = (rows, total)
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), repos


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
    client, repos = _client(monkeypatch, [_wf_row(is_template=True)], total=1)
    resp = client.get("/workflows?isTemplate=true")
    assert resp.status_code == 200
    kwargs = repos.list_page.await_args.kwargs
    assert kwargs["is_template"] is True
    assert kwargs["is_public"] is None and kwargs["category"] is None
    assert kwargs["viewer"] == {"user_id": "dev", "is_admin": False}


def test_list_workflows_mine_filters_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch, [_wf_row(user_id="dev")], total=1)
    resp = client.get("/workflows?mine=true")
    assert resp.status_code == 200
    # dev-mode fallback (ADR-0015) sets user_id='dev'
    kwargs = repos.list_page.await_args.kwargs
    assert kwargs["mine"] is True
    assert kwargs["viewer"]["user_id"] == "dev"


def test_list_workflows_mine_overrides_admin_global_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: admins asking for mine=true must NOT get the global
    feed.  The /designer (My workflows) page sets mine=true, and an
    admin viewing it was seeing every workflow on the system —
    including user_id=null templates — because the listing skipped the
    authz filter for admins.  mine=true is an explicit "scope to me"
    request and must apply regardless of role."""
    from src.security.auth import get_current_role

    client, repos = _client(monkeypatch, [_wf_row(user_id="dev")], total=1)
    # Use FastAPI's dependency override (same pattern as the existing
    # admin-bypass tests below).  Without it the dev-mode caller resolves to
    # 'member'.
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.get("/workflows?mine=true")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    kwargs = repos.list_page.await_args.kwargs
    # Scoped to "me" (owner OR assignee), not the global "see all" the bug produced.
    assert kwargs["mine"] is True, "admin + mine=true must apply the mine scope"
    assert kwargs["viewer"] == {"user_id": "dev", "is_admin": True}


def test_list_workflows_admin_without_mine_sees_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counterpart to the above: admin without mine=true keeps the
    original "see all workflows" behaviour (the global feed is what
    /admin/workflows uses).  Make sure my fix didn't accidentally
    break that path."""
    from src.security.auth import get_current_role

    client, repos = _client(monkeypatch, [], total=0)
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.get("/workflows")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    kwargs = repos.list_page.await_args.kwargs
    # No mine scope for an admin's global view; the repo applies no visibility filter.
    assert kwargs["mine"] is False
    assert kwargs["viewer"] == {"user_id": "dev", "is_admin": True}


def test_list_workflows_mine_includes_assigned_not_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch, [_wf_row(user_id="owner-other", id="w-shared")], total=1)
    resp = client.get("/workflows?mine=true")
    assert resp.status_code == 200
    body = resp.json()
    assert [w["id"] for w in body["items"]] == ["w-shared"]
    # The owner-or-assignee predicate lives in the repo (EXISTS; see the repo integration tests).
    kwargs = repos.list_page.await_args.kwargs
    assert kwargs["mine"] is True and kwargs["viewer"]["user_id"] == "dev"


def test_list_workflows_default_view_includes_assigned_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _client(monkeypatch, [_wf_row(user_id="owner-other")], total=1)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    kwargs = repos.list_page.await_args.kwargs
    assert kwargs["mine"] is False
    assert kwargs["viewer"] == {"user_id": "dev", "is_admin": False}


def test_list_workflows_limit_over_100_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows?limit=150")
    assert resp.status_code == 422


def test_search_workflows_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _client(monkeypatch, [_wf_row(name="SearchTest")], total=1)
    resp = client.get("/workflows/search?q=search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "SearchTest"
    kwargs = repos.search.await_args.kwargs
    assert kwargs["q"] == "search" and kwargs["limit"] == 50
    assert kwargs["viewer"] == {"user_id": "dev", "is_admin": False}


def test_search_workflows_includes_assigned_not_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counterpart to test_list_workflows_default_view_includes_assigned_private:
    a workflow the caller has via WorkflowAssignment (not ownership, not
    public) must still be findable through the search box, not just in
    the default /workflows listing."""
    client, repos = _client(
        monkeypatch, [_wf_row(user_id="owner-other", name="SearchTest")], total=1
    )
    resp = client.get("/workflows/search?q=search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "SearchTest"
    # Assigned-but-not-owned visibility is the repo's EXISTS predicate (repo integration tests).
    kwargs = repos.search.await_args.kwargs
    assert kwargs["viewer"] == {"user_id": "dev", "is_admin": False}


def test_search_workflows_empty_q_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows/search?q=")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 2 — GET /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def _client_fetch(
    monkeypatch: pytest.MonkeyPatch, row: Any | None
) -> tuple[TestClient, WorkflowRepoMocks]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = row
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), repos


def test_get_workflow_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _wf_row(id="wx", name="Hello", user_id="dev")  # match dev-mode caller
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
) -> tuple[TestClient, WorkflowRepoMocks]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = existing
    repos.update.return_value = updated
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), repos


_VALID_BODY: dict[str, Any] = {
    "name": "Updated",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


def test_put_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", user_id="dev")  # dev-mode fallback user
    updated = _wf_row(id="w1", user_id="dev", name="Updated")
    client, _ = _client_put(monkeypatch, existing, updated)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Updated"


def test_put_passes_the_precondition_to_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", user_id="dev")
    client, repos = _client_put(monkeypatch, existing, _wf_row(id="w1", user_id="dev"))
    resp = client.put(
        "/workflows/w1", json={**_VALID_BODY, "expectedUpdatedAt": "2026-11-24T10:00:00.123Z"}
    )
    assert resp.status_code == 200, resp.text
    kwargs = repos.update.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["expected_updated_at"] == datetime(2026, 11, 24, 10, 0, 0, 123000, tzinfo=UTC)


def test_put_truncates_a_sub_millisecond_precondition_to_stored_precision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The column keeps milliseconds, so ``.123789`` must match a stored ``.123``."""
    existing = _wf_row(id="w1", user_id="dev")
    client, repos = _client_put(monkeypatch, existing, _wf_row(id="w1", user_id="dev"))
    resp = client.put(
        "/workflows/w1", json={**_VALID_BODY, "expectedUpdatedAt": "2026-11-24T10:00:00.123789Z"}
    )
    assert resp.status_code == 200, resp.text
    kwargs = repos.update.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["expected_updated_at"] == datetime(2026, 11, 24, 10, 0, 0, 123000, tzinfo=UTC)


def test_put_by_a_stranger_with_a_stale_precondition_is_403_not_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _wf_row(id="w1", user_id="someone-else")
    client, repos = _client_put(monkeypatch, existing, None)
    repos.update.side_effect = StalePrecondition(datetime(2026, 11, 24, 10, 5, tzinfo=UTC))
    resp = client.put(
        "/workflows/w1", json={**_VALID_BODY, "expectedUpdatedAt": "2026-11-24T10:00:00Z"}
    )
    assert resp.status_code == 403
    assert "currentUpdatedAt" not in resp.text
    repos.update.assert_not_awaited()


def test_put_without_a_precondition_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", user_id="dev")
    client, repos = _client_put(monkeypatch, existing, _wf_row(id="w1", user_id="dev"))
    assert client.put("/workflows/w1", json=_VALID_BODY).status_code == 200
    assert repos.update.await_args.kwargs["expected_updated_at"] is None  # type: ignore[union-attr]


def test_put_with_a_stale_precondition_is_409_with_the_current_updated_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _wf_row(id="w1", user_id="dev")
    client, repos = _client_put(monkeypatch, existing, None)
    current = datetime(2026, 11, 24, 10, 5, 0, 500000, tzinfo=UTC)
    repos.update.side_effect = StalePrecondition(current)
    resp = client.put(
        "/workflows/w1", json={**_VALID_BODY, "expectedUpdatedAt": "2026-11-24T10:00:00Z"}
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["message"] == "workflow changed since you loaded it"
    assert detail["code"] == "workflow_changed"
    assert datetime.fromisoformat(detail["currentUpdatedAt"].replace("Z", "+00:00")) == current


_DUP_EDGE_BODY: dict[str, Any] = {
    **_VALID_BODY,
    "edges": [
        {"id": "e1", "source": "s", "target": "e"},
        {"id": "e2", "source": "s", "target": "e"},
    ],
}


def test_put_workflow_collapses_duplicate_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    """A canvas holding duplicate edges must still save, stored without the copies."""
    existing = _wf_row(id="w1", user_id="dev")
    updated = _wf_row(id="w1", user_id="dev", name="Updated")
    client, repos = _client_put(monkeypatch, existing, updated)
    resp = client.put("/workflows/w1", json=_DUP_EDGE_BODY)
    assert resp.status_code == 200, resp.text
    stored = repos.update.await_args.args[2]["edges"]
    assert [e["id"] for e in stored] == ["e1"]


def test_post_workflow_collapses_duplicate_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.create.return_value = _wf_row_create(user_id="dev")
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    client = TestClient(app)

    resp = client.post("/workflows", json=_DUP_EDGE_BODY)
    assert resp.status_code == 201, resp.text
    call = repos.create.await_args
    assert call is not None
    stored = call.args[1]["edges"]
    assert [e["id"] for e in stored] == ["e1"]


def test_put_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", user_id="someone-else")
    client, _ = _client_put(monkeypatch, existing, None)
    resp = client.put("/workflows/w1", json=_VALID_BODY)
    assert resp.status_code == 403


def test_put_workflow_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_put(monkeypatch, None, None)
    resp = client.put("/workflows/ghost", json=_VALID_BODY)
    assert resp.status_code == 404


def test_put_workflow_invalid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """No start node → validator raises → 422."""
    existing = _wf_row(id="w1", user_id="dev")
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
) -> tuple[TestClient, WorkflowRepoMocks]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = existing
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), repos


def test_delete_workflow_owner_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", user_id="dev")
    client, repos = _client_delete(monkeypatch, existing)
    resp = client.delete("/workflows/w1")
    assert resp.status_code == 204
    repos.delete.assert_awaited_once()


def test_delete_workflow_not_owner_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _wf_row(id="w1", user_id="other")
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
    row = _wf_row(id="wx", user_id="someone-else", is_public=False)
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 404


def test_get_workflow_public_non_owner_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public workflows are readable by non-owners."""
    row = _wf_row(id="wx", user_id="other", is_public=True, name="Public")
    client, _ = _client_fetch(monkeypatch, row)
    resp = client.get("/workflows/wx")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Public"


def test_list_workflows_where_clause_has_authz_or(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List query must OR(is_public=true, user_id=caller)."""
    client, repos = _client(monkeypatch, [], total=0)
    resp = client.get("/workflows")
    assert resp.status_code == 200
    # The repo applies is_public OR owner OR assigned for a non-admin viewer.
    kwargs = repos.list_page.await_args.kwargs
    assert kwargs["viewer"] == {"user_id": "dev", "is_admin": False}
    assert kwargs["mine"] is False


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
    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.create.return_value = _wf_row_create(id="w-big", user_id="dev")
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
    override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.create.return_value = _wf_row_create(id="w-big", user_id="dev")
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

    row = _wf_row(id="wx", user_id="someone-else", is_public=False, name="Private")
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

    existing = _wf_row(id="w1", user_id="someone-else")
    updated = _wf_row(id="w1", user_id="someone-else", name="Updated")
    client, _ = _client_put(monkeypatch, existing, updated)
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.put("/workflows/w1", json=_VALID_BODY)
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated"


def test_admin_can_delete_any_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admins can delete any workflow (admin panel requirement, Phase 10)."""
    from src.security.auth import get_current_role

    existing = _wf_row(id="w1", user_id="someone-else")
    client, repos = _client_delete(monkeypatch, existing)
    client.app.dependency_overrides[get_current_role] = lambda: ("admin-user", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.delete("/workflows/w1")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 204
    repos.delete.assert_awaited_once()


def _client_patch_owner(
    monkeypatch: pytest.MonkeyPatch,
    wf_row: Any | None,
    target_user: Any | None,
    updated_row: Any | None,
) -> tuple[TestClient, WorkflowRepoMocks]:
    """Helper for PATCH /workflows/{id}/owner tests (admin-only endpoint)."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    app.state.session = override_session(app)
    repos = patch_workflow_repos(monkeypatch)
    repos.get.return_value = wf_row
    repos.update.return_value = updated_row
    target = (
        make_user(id=target_user.id, email=target_user.email) if target_user is not None else None
    )
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=target))
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=target))
    app.state.checkpointer = MagicMock()
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
    return TestClient(app), repos


def test_patch_workflow_owner_by_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin PATCHes ownership to a user identified by userId."""
    from src.security.auth import ensure_admin

    target = SimpleNamespace(id="new-owner", email="target@x.com")
    wf = _wf_row(id="wx", user_id="old-owner")
    updated = _wf_row(id="wx", user_id="new-owner")
    client, repos = _client_patch_owner(monkeypatch, wf, target, updated)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/workflows/wx/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    repos.update.assert_awaited_once()
    assert repos.update.await_args.args[1:] == ("wx", {"user_id": "new-owner"})  # type: ignore[union-attr]
    client.app.state.session.commit.assert_awaited_once()  # type: ignore[attr-defined]


def test_patch_workflow_owner_writes_before_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.security.auth import ensure_admin

    target = SimpleNamespace(id="new-owner", email="target@x.com")
    wf = _wf_row(id="wx", user_id="old-owner")
    client, repos = _client_patch_owner(monkeypatch, wf, target, _wf_row(id="wx"))
    order: list[str] = []
    real_update = repos.update.side_effect

    async def _update(*_a: object, **_k: object) -> Any:
        order.append("update")
        return _wf_row(id="wx", user_id="new-owner")

    async def _commit() -> None:
        order.append("commit")

    del real_update
    repos.update.side_effect = _update
    client.app.state.session.commit.side_effect = _commit  # type: ignore[attr-defined]
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/workflows/wx/owner", json={"userId": "new-owner"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert order == ["update", "commit"]


def test_patch_workflow_owner_by_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin PATCHes ownership to a user identified by email."""
    from src.security.auth import ensure_admin

    target = SimpleNamespace(id="uid-email", email="target@x.com")
    wf = _wf_row(id="wx", user_id="old-owner")
    updated = _wf_row(id="wx", user_id="uid-email")
    client, repos = _client_patch_owner(monkeypatch, wf, target, updated)
    client.app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]
    try:
        resp = client.patch("/workflows/wx/owner", json={"email": "target@x.com"})
    finally:
        client.app.dependency_overrides.pop(ensure_admin, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    repos.update.assert_awaited_once()
    assert repos.update.await_args.args[1:] == ("wx", {"user_id": "uid-email"})  # type: ignore[union-attr]
    client.app.state.session.commit.assert_awaited_once()  # type: ignore[attr-defined]


def test_patch_workflow_owner_unknown_email_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH with unknown email → 404."""
    from src.security.auth import ensure_admin

    wf = _wf_row(id="wx", user_id="old-owner")
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

    Nothing overrides get_current_role here, so the dev-mode caller resolves to
    ('dev', 'member') and ensure_admin raises 403.
    """
    wf = _wf_row(id="wx", user_id="old-owner")
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
    """Setting is_production=True without externalSlug → 422."""
    existing = _wf_row(id="w1", user_id="dev")
    client, _ = _client_put(monkeypatch, existing, None)
    body: dict[str, Any] = {**_VALID_BODY, "isProduction": True}
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 422
    assert "external_slug is required" in resp.json()["detail"]


def test_put_publish_invalid_slug_format_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid slug (uppercase, starts with hyphen) → 422."""
    existing = _wf_row(id="w1", user_id="dev")
    client, _ = _client_put(monkeypatch, existing, None)
    for bad_slug in ("-bad-slug", "UPPERCASE", "has space", "a"):
        body: dict[str, Any] = {**_VALID_BODY, "isProduction": True, "externalSlug": bad_slug}
        resp = client.put("/workflows/w1", json=body)
        assert resp.status_code == 422, (
            f"expected 422 for slug {bad_slug!r}, got {resp.status_code}"
        )


def test_put_publish_rejects_unknown_variable_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """P0-1: publishing a workflow that references an undeclared
    {{variable}} is rejected with a 422 naming the bad placeholder — the
    exact live bug this guards against (Start input `MB`, a downstream
    node referencing `{{jira_project_key}}`)."""
    existing = _wf_row(id="w1", user_id="dev")
    client, _ = _client_put(monkeypatch, existing, None)
    body: dict[str, Any] = {
        "name": "Updated",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 50, "y": 0},
                "data": {"label": "A", "instructions": "{{undeclared_var}}"},
            },
            {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "a"},
            {"id": "e2", "source": "a", "target": "e"},
        ],
        "isProduction": True,
        "externalSlug": "my-workflow",
    }
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 422, resp.text
    assert "undeclared_var" in resp.text


def test_put_publish_sets_isproduction_and_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid publish request updates both isProduction and externalSlug."""
    existing = _wf_row(id="w1", user_id="dev")
    updated = _wf_row(
        id="w1",
        user_id="dev",
        name="Updated",
        is_production=True,
        external_slug="my-workflow",
    )
    client, repos = _client_put(monkeypatch, existing, updated)
    resp = client.put("/workflows/w1", json=_PUBLISH_BODY)
    assert resp.status_code == 200, resp.text
    call_data = repos.update.await_args.args[2]
    assert call_data["is_production"] is True
    assert call_data["external_slug"] == "my-workflow"


def test_put_unpublish_clears_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """isProduction=False clears externalSlug (sets to None)."""
    existing = _wf_row(id="w1", user_id="dev", is_production=True, external_slug="my-workflow")
    updated = _wf_row(
        id="w1", user_id="dev", name="Updated", is_production=False, external_slug=None
    )
    client, repos = _client_put(monkeypatch, existing, updated)
    body: dict[str, Any] = {**_VALID_BODY, "isProduction": False}
    resp = client.put("/workflows/w1", json=body)
    assert resp.status_code == 200, resp.text
    call_data = repos.update.await_args.args[2]
    assert call_data["is_production"] is False
    assert call_data["external_slug"] is None


def test_put_slug_conflict_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """A unique violation on the slug becomes a 409 naming only the constraint."""
    existing = _wf_row(id="w1", user_id="dev")
    client, repos = _client_put(monkeypatch, existing, None)
    repos.update.side_effect = AlreadyExists("workflows_external_slug_key")

    resp = client.put("/workflows/w1", json=_PUBLISH_BODY)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail.startswith("external_slug already in use: ")
    assert "workflows_external_slug_key" in detail
    assert "[SQL" not in detail and "parameters" not in detail
