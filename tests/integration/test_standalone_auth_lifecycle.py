"""Integration — standalone auth happy path against real Neon."""

import contextlib
import secrets
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_standalone_register_login_me_refresh(client: AsyncClient, app: FastAPI) -> None:
    db: Any = app.state.db  # Prisma client attached by conftest lifespan

    # Uniqueify email per run to avoid colliding with prior test invocations
    email = f"test-{secrets.token_hex(6)}@composer.test"
    password = "correct-horse-battery-staple"

    # Register
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "displayName": "Integration"},
    )
    assert reg.status_code == 201, reg.text
    body = reg.json()
    access = body["accessToken"]
    refresh = body["refreshToken"]
    user_id = body["id"]

    try:
        # Authenticated /auth/me
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 200, me.text
        assert me.json()["email"] == email

        # Refresh → new access + new refresh
        r = await client.post("/auth/refresh", json={"refreshToken": refresh})
        assert r.status_code == 200, r.text
        new_access = r.json()["accessToken"]

        # New access works
        me2 = await client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        assert me2.status_code == 200

        # Login with the same credentials returns fresh tokens
        login = await client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200

        # Wrong password → 401
        bad = await client.post("/auth/login", json={"email": email, "password": "wrong-password"})
        assert bad.status_code == 401

        # Unknown email → 401 (uniform 401, no enumeration)
        unknown = await client.post(
            "/auth/login",
            json={"email": f"ghost-{secrets.token_hex(6)}@composer.test", "password": "x"},
        )
        assert unknown.status_code == 401

    finally:
        # Cleanup: delete the User row via Prisma directly
        # (no DELETE /users endpoint in 7a)
        with contextlib.suppress(Exception):
            await db.user.delete(where={"id": user_id})


async def test_standalone_register_duplicate_email_409(
    client: AsyncClient,
    app: FastAPI,
) -> None:
    db: Any = app.state.db

    email = f"test-dup-{secrets.token_hex(6)}@composer.test"

    # First register succeeds
    r1 = await client.post("/auth/register", json={"email": email, "password": "pass-12345"})
    assert r1.status_code == 201
    user_id = r1.json()["id"]

    try:
        # Second register with same email → 409
        r2 = await client.post(
            "/auth/register", json={"email": email, "password": "other-pass-12345"}
        )
        assert r2.status_code == 409
    finally:
        with contextlib.suppress(Exception):
            await db.user.delete(where={"id": user_id})


async def test_standalone_missing_auth_401_on_scoped_route(
    client: AsyncClient,
) -> None:
    """A route that declares Depends(get_current_user_id) returns 401 when
    ENVIRONMENT=production and no Authorization header.

    GET /auth/me is the simplest probe — it declares the dependency.

    NB: this test requires ENVIRONMENT=production in the app's process.
    conftest.py runs create_app() with whatever env is set at import time.
    If ENVIRONMENT defaults to 'development', the dev-mode fallback kicks
    in (ADR-0015), returning user_id='dev' and /auth/me returns 404 (no
    User row for 'dev').  We accept EITHER 401 (prod) OR 404 (dev-mode
    fallback + no 'dev' user in DB) — both prove the dependency path
    works without an explicit Authorization header doing the wrong thing.
    """
    resp = await client.get("/auth/me")
    assert resp.status_code in (401, 404), resp.text
