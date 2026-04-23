"""Integration — publish a workflow, create API key, invoke externally (Phase 10a)."""

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


async def test_publish_and_external_invoke(client: AsyncClient, app: FastAPI) -> None:
    db: Any = app.state.db
    email = f"p10a-{secrets.token_hex(6)}@example.com"
    password = "correct-horse-battery-staple"
    user_id: str | None = None
    wf_id: str | None = None
    slug = f"test-wf-{secrets.token_hex(4)}"

    try:
        r = await client.post("/auth/register", json={"email": email, "password": password})
        assert r.status_code == 201, r.text
        token = r.json()["accessToken"]
        user_id = r.json()["id"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            "/workflows", json={"name": "Ext WF", **_MINIMAL}, headers=auth_headers
        )
        assert r.status_code == 201, r.text
        wf_id = r.json()["id"]

        # Publish.
        r = await client.put(
            f"/workflows/{wf_id}",
            json={
                "name": "Ext WF",
                "isProduction": True,
                "externalSlug": slug,
                **_MINIMAL,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["isProduction"] is True
        assert body["externalSlug"] == slug

        # Create an API key.
        r = await client.post("/api-keys", json={"label": "caller"}, headers=auth_headers)
        assert r.status_code == 201, r.text
        api_key = r.json()["key"]

        # External invoke (async).
        r = await client.post(
            f"/api/run/{slug}",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": {}},
        )
        assert r.status_code == 200, r.text
        invoke_body = r.json()
        assert "executionId" in invoke_body
        assert invoke_body["streamUrl"].endswith("/ws")

        # Revoke the key.
        keys = (await client.get("/api-keys", headers=auth_headers)).json()
        key_id = next(k["id"] for k in keys if api_key.startswith(k["keyPrefix"]))
        r = await client.delete(f"/api-keys/{key_id}", headers=auth_headers)
        assert r.status_code == 204

        # Revoked key rejected.
        r = await client.post(
            f"/api/run/{slug}",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": {}},
        )
        assert r.status_code == 401
    finally:
        if wf_id:
            with contextlib.suppress(Exception):
                await db.workflow.delete(where={"id": wf_id})
        if user_id:
            with contextlib.suppress(Exception):
                await db.user.delete(where={"id": user_id})
