"""Integration -- a required branch left unconnected.

Product rule ("warn on save, block run"): a half-built canvas still SAVES
(with a warning), but running, publishing and the external invoke are
refused up front with the user-facing message, and no execution row exists.
"""

import contextlib
import secrets
from typing import Any

import pytest
from httpx import AsyncClient

from src.db.models import User, Workflow, WorkflowExecution
from tests.integration._rows import Rows

pytestmark = pytest.mark.integration

_NODES: list[dict[str, Any]] = [
    {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
    {
        "id": "approval-1",
        "type": "user-approval",
        "position": {"x": 100, "y": 0},
        "data": {"label": "approval-1", "approvalMessage": "ok?"},
    },
    {"id": "end-1", "type": "end", "position": {"x": 200, "y": 0}, "data": {"label": "E"}},
]
_START = {"id": "e0", "source": "s", "target": "approval-1"}
_APPROVED = {"id": "e1", "source": "approval-1", "target": "end-1", "branch": "approved"}
_REJECTED = {"id": "e2", "source": "approval-1", "target": "end-1", "branch": "rejected"}

HALF = {"name": "half built", "nodes": _NODES, "edges": [_START, _APPROVED]}
FULL = {"name": "complete", "nodes": _NODES, "edges": [_START, _APPROVED, _REJECTED]}
MESSAGE = 'User approval step "approval-1" has no "rejected" path.'


async def test_half_built_canvas_saves_with_a_warning(client: AsyncClient) -> None:
    created = await client.post("/workflows", json=HALF)
    assert created.status_code == 201, created.text
    assert any(MESSAGE in w for w in created.json()["warnings"])

    wf_id = created.json()["id"]
    updated = await client.put(f"/workflows/{wf_id}", json=HALF)
    assert updated.status_code == 200, updated.text
    assert any(MESSAGE in w for w in updated.json()["warnings"])

    fetched = await client.get(f"/workflows/{wf_id}")
    assert any(MESSAGE in w for w in fetched.json()["warnings"])


async def test_complete_canvas_saves_without_warnings_and_runs(client: AsyncClient) -> None:
    created = await client.post("/workflows", json=FULL)
    assert created.status_code == 201, created.text
    assert created.json()["warnings"] == []
    started = await client.post(
        "/executions", json={"workflowId": created.json()["id"], "input": ""}
    )
    assert started.status_code == 202, started.text


async def test_run_is_refused_before_any_execution_row_exists(
    client: AsyncClient, rows: Rows
) -> None:
    wf_id = (await client.post("/workflows", json=HALF)).json()["id"]
    before = await rows.count(WorkflowExecution, WorkflowExecution.workflow_id == wf_id)

    resp = await client.post("/executions", json={"workflowId": wf_id, "input": ""})

    assert resp.status_code == 422, resp.text
    assert MESSAGE in resp.json()["detail"]
    assert (
        await rows.count(WorkflowExecution, WorkflowExecution.workflow_id == wf_id) == before == 0
    )


async def test_publish_is_refused_then_allowed_once_complete(client: AsyncClient) -> None:
    wf_id = (await client.post("/workflows", json=HALF)).json()["id"]
    slug = f"rb-{secrets.token_hex(4)}"

    refused = await client.put(
        f"/workflows/{wf_id}", json={**HALF, "isProduction": True, "externalSlug": slug}
    )
    assert refused.status_code == 422, refused.text
    assert MESSAGE in refused.json()["detail"]
    assert (await client.get(f"/workflows/{wf_id}")).json()["isProduction"] is False

    allowed = await client.put(
        f"/workflows/{wf_id}", json={**FULL, "isProduction": True, "externalSlug": slug}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["isProduction"] is True


async def test_admin_promote_is_refused_for_an_unrunnable_workflow(
    client: AsyncClient, rows: Rows
) -> None:
    user_id: str | None = None
    wf_id: str | None = None
    try:
        r = await client.post(
            "/auth/register",
            json={
                "email": f"rb-{secrets.token_hex(6)}@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert r.status_code == 201, r.text
        user_id = r.json()["id"]
        headers = {"Authorization": f"Bearer {r.json()['accessToken']}"}
        await rows.set(User, User.id == user_id, role="admin")
        wf_id = (await client.post("/workflows", json=HALF, headers=headers)).json()["id"]

        resp = await client.patch(
            f"/workflows/{wf_id}/admin-flags",
            json={"isProduction": True, "externalSlug": f"rb-{secrets.token_hex(4)}"},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert MESSAGE in resp.json()["detail"]
    finally:
        if wf_id:
            with contextlib.suppress(Exception):
                await rows.remove(Workflow, Workflow.id == wf_id)
        if user_id:
            with contextlib.suppress(Exception):
                await rows.remove(User, User.id == user_id)


async def test_external_invoke_is_refused_for_a_published_unrunnable_workflow(
    client: AsyncClient, rows: Rows
) -> None:
    email = f"rb-{secrets.token_hex(6)}@example.com"
    user_id: str | None = None
    wf_id: str | None = None
    slug = f"rb-{secrets.token_hex(4)}"
    try:
        r = await client.post(
            "/auth/register", json={"email": email, "password": "correct-horse-battery-staple"}
        )
        assert r.status_code == 201, r.text
        headers = {"Authorization": f"Bearer {r.json()['accessToken']}"}
        user_id = r.json()["id"]
        wf_id = (await client.post("/workflows", json=FULL, headers=headers)).json()["id"]
        r = await client.put(
            f"/workflows/{wf_id}",
            json={**FULL, "isProduction": True, "externalSlug": slug},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        api_key = (await client.post("/api-keys", json={"label": "c"}, headers=headers)).json()[
            "key"
        ]
        # Legacy / hand-edited row: published, then a branch goes missing.
        await rows.set(Workflow, Workflow.id == wf_id, edges=[_START, _APPROVED])

        r = await client.post(
            f"/api/run/{slug}", headers={"Authorization": f"Bearer {api_key}"}, json={"input": {}}
        )
        assert r.status_code == 422, r.text
        assert MESSAGE in r.json()["detail"]
        assert await rows.count(WorkflowExecution, WorkflowExecution.workflow_id == wf_id) == 0
    finally:
        if wf_id:
            with contextlib.suppress(Exception):
                await rows.remove(Workflow, Workflow.id == wf_id)
        if user_id:
            with contextlib.suppress(Exception):
                await rows.remove(User, User.id == user_id)
