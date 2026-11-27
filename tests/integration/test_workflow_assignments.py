"""Integration — workflow assignment grant/revoke against real Neon."""

import contextlib
import os
import secrets
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient

from src.db.models import User
from tests.integration._rows import Rows

pytestmark = pytest.mark.integration

_MINIMAL_BODY: dict[str, Any] = {
    "name": "Assignment integration test",
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


async def test_grant_and_revoke_assignment_cycle(client: AsyncClient, rows: Rows) -> None:
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

    # The dev-mode identity ('dev') is the granter, and assigned_by_id now has a foreign key
    # to users, so it needs a row (production granters always have one).
    created_dev = False
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        created_dev = (
            conn.execute(
                "INSERT INTO users (id, email, updated_at) VALUES ('dev', %s, now()) "
                "ON CONFLICT DO NOTHING",
                (f"dev-{secrets.token_hex(4)}@example.com",),
            ).rowcount
            == 1
        )

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
            await rows.remove(User, User.id == target_user_id)
        if created_dev:
            with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
                conn.execute("DELETE FROM users WHERE id = 'dev'")
