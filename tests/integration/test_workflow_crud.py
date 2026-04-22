"""Integration — full workflow CRUD cycle against real Neon (Phase 7b)."""

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


_MINIMAL_BODY: dict[str, Any] = {
    "name": "Phase 7b CRUD cycle",
    "description": "Integration test",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


async def test_workflow_crud_cycle(client: AsyncClient) -> None:
    # Create
    r = await client.post("/workflows", json=_MINIMAL_BODY)
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    # Get
    r = await client.get(f"/workflows/{wf_id}")
    assert r.status_code == 200
    assert r.json()["name"] == _MINIMAL_BODY["name"]

    # List — new workflow appears somewhere
    r = await client.get("/workflows?mine=true&limit=100")
    assert r.status_code == 200
    ids = [w["id"] for w in r.json()["items"]]
    assert wf_id in ids

    # Search
    r = await client.get("/workflows/search?q=Phase+7b")
    assert r.status_code == 200
    assert any(w["id"] == wf_id for w in r.json()["items"])

    # Update
    updated = {**_MINIMAL_BODY, "name": "Updated name"}
    r = await client.put(f"/workflows/{wf_id}", json=updated)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Updated name"

    # Confirm the update
    r = await client.get(f"/workflows/{wf_id}")
    assert r.json()["name"] == "Updated name"

    # Delete
    r = await client.delete(f"/workflows/{wf_id}")
    assert r.status_code == 204

    # Confirm 404
    r = await client.get(f"/workflows/{wf_id}")
    assert r.status_code == 404
