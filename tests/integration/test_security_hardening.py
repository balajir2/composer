"""Integration — two users, authz boundaries against real Neon (Phase 8).

Exercises the authz invariants from ADR-0021 end-to-end:
- User B can read A's public workflow (200)
- User B cannot read A's private workflow (404, info-leak tight)
- User B cannot PUT/DELETE A's workflow (403)
- User B cannot read A's execution (404)
"""

import contextlib
import secrets
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration


_MINIMAL: dict[str, Any] = {
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


async def test_two_user_authz_boundaries(client: AsyncClient, app: FastAPI) -> None:
    db: Any = app.state.db

    user_a_email = f"phase8-a-{secrets.token_hex(6)}@example.com"
    user_b_email = f"phase8-b-{secrets.token_hex(6)}@example.com"
    password = "correct-horse-battery-staple"
    user_a_id: str | None = None
    user_b_id: str | None = None
    created_workflow_ids: list[str] = []

    try:
        # Register both users
        r_a = await client.post(
            "/auth/register",
            json={"email": user_a_email, "password": password, "displayName": "A"},
        )
        assert r_a.status_code == 201, r_a.text
        token_a = r_a.json()["accessToken"]
        user_a_id = r_a.json()["id"]

        r_b = await client.post(
            "/auth/register",
            json={"email": user_b_email, "password": password, "displayName": "B"},
        )
        assert r_b.status_code == 201, r_b.text
        token_b = r_b.json()["accessToken"]
        user_b_id = r_b.json()["id"]

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # A creates a private workflow
        r = await client.post(
            "/workflows",
            json={"name": "A private", "isPublic": False, **_MINIMAL},
            headers=headers_a,
        )
        assert r.status_code == 201, r.text
        wf_private_id = r.json()["id"]
        created_workflow_ids.append(wf_private_id)

        # A creates a public workflow
        r = await client.post(
            "/workflows",
            json={"name": "A public", "isPublic": True, **_MINIMAL},
            headers=headers_a,
        )
        assert r.status_code == 201, r.text
        wf_public_id = r.json()["id"]
        created_workflow_ids.append(wf_public_id)

        # B reads A's public → 200
        r = await client.get(f"/workflows/{wf_public_id}", headers=headers_b)
        assert r.status_code == 200, r.text

        # B reads A's private → 404 (info-leak tight)
        r = await client.get(f"/workflows/{wf_private_id}", headers=headers_b)
        assert r.status_code == 404, r.text

        # B tries to PUT A's public → 403
        r = await client.put(
            f"/workflows/{wf_public_id}",
            json={"name": "hacked", **_MINIMAL},
            headers=headers_b,
        )
        assert r.status_code == 403, r.text

        # B tries to DELETE A's public → 403
        r = await client.delete(f"/workflows/{wf_public_id}", headers=headers_b)
        assert r.status_code == 403, r.text

        # A's list scoped to owned returns both workflows
        r = await client.get("/workflows?mine=true&limit=100", headers=headers_a)
        assert r.status_code == 200, r.text
        ids_a = {w["id"] for w in r.json()["items"]}
        assert wf_private_id in ids_a
        assert wf_public_id in ids_a

        # B's list with mine=true excludes A's workflows entirely
        r = await client.get("/workflows?mine=true&limit=100", headers=headers_b)
        assert r.status_code == 200, r.text
        ids_b = {w["id"] for w in r.json()["items"]}
        assert wf_private_id not in ids_b
        assert wf_public_id not in ids_b

        # B's default list includes A's public (world-readable) but NOT A's private
        r = await client.get("/workflows?limit=100", headers=headers_b)
        assert r.status_code == 200, r.text
        ids_b_default = {w["id"] for w in r.json()["items"]}
        assert wf_public_id in ids_b_default
        assert wf_private_id not in ids_b_default

        # A executes the private workflow
        r = await client.post(
            "/executions",
            json={"workflowId": wf_private_id, "input": {}},
            headers=headers_a,
        )
        assert r.status_code in (201, 202), r.text
        exec_id = r.json()["id"]

        # B reads A's execution → 404
        r = await client.get(f"/executions/{exec_id}", headers=headers_b)
        assert r.status_code == 404, r.text

        # B's list of executions excludes A's
        r = await client.get("/executions?limit=100", headers=headers_b)
        assert r.status_code == 200, r.text
        exec_ids_b = {e["id"] for e in r.json()["items"]}
        assert exec_id not in exec_ids_b

    finally:
        # Cleanup: delete workflows A created (cascades executions)
        for wid in created_workflow_ids:
            with contextlib.suppress(Exception):
                await db.workflow.delete(where={"id": wid})
        # Cleanup users
        if user_a_id:
            with contextlib.suppress(Exception):
                await db.user.delete(where={"id": user_a_id})
        if user_b_id:
            with contextlib.suppress(Exception):
                await db.user.delete(where={"id": user_b_id})
