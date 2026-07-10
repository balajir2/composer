"""Integration — workflow assignment grant/revoke against real Neon."""

import contextlib
import secrets
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration

_MINIMAL_BODY: dict[str, Any] = {
    "name": "Assignment integration test",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


async def test_grant_and_revoke_assignment_cycle(client: AsyncClient, app: FastAPI) -> None:
    db: Any = app.state.db

    # `grant_workflow_assignment` requires the target user to exist as a
    # real row (src/api/workflows.py does `db.user.find_unique` and 404s
    # otherwise) — dev-mode auth fallback only gives us one identity
    # ('dev'), so register a real throwaway second user via /auth/register
    # to get a genuine user id, mirroring the cleanup pattern used in
    # tests/integration/test_standalone_auth_lifecycle.py.
    email = f"test-assignee-{secrets.token_hex(6)}@example.com"
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    assert reg.status_code == 201, reg.text
    target_user_id = reg.json()["id"]

    wf_id: str | None = None
    try:
        r = await client.post("/workflows", json=_MINIMAL_BODY)
        assert r.status_code == 201, r.text
        wf_id = r.json()["id"]

        # Grant to the real second user.
        r = await client.post(f"/workflows/{wf_id}/assignments/{target_user_id}")
        assert r.status_code == 201, r.text

        r = await client.get(f"/workflows/{wf_id}/assignments")
        assert r.status_code == 200
        assert any(a["userId"] == target_user_id for a in r.json())

        r = await client.delete(f"/workflows/{wf_id}/assignments/{target_user_id}")
        assert r.status_code == 204

        r = await client.get(f"/workflows/{wf_id}/assignments")
        assert r.json() == []
    finally:
        if wf_id is not None:
            with contextlib.suppress(Exception):
                await client.delete(f"/workflows/{wf_id}")
        with contextlib.suppress(Exception):
            await db.user.delete(where={"id": target_user_id})
