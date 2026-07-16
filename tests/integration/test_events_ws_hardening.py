"""Integration — WebSocket authz boundaries against real Neon (Phase 9a).

Uses Starlette's sync `TestClient` for both setup (register + workflow + execution)
and WebSocket connect.  Mixing httpx.AsyncClient (async loop) with TestClient
(worker-thread WebSocket) deadlocks Prisma queries whose connection is bound to
a different loop, so this integration stays fully-sync end-to-end.  The Prisma
lifespan is managed by TestClient's context-manager entry.
"""

import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _drive_cloud_tasks_synchronously() -> None:  # pyright: ignore[reportUnusedFunction]
    """Shadow tests/integration/conftest.py's async, `app`-fixture-dependent
    fixture of the same name for this module.

    That fixture's mere signature (an `app: FastAPI` parameter) forces
    pytest to instantiate the shared async `app` fixture -- a second,
    independent Prisma-connected app/lifespan -- even for tests, like this
    one, that never request it. This module builds its own `app` via
    `create_app()` and drives everything through a single synchronous
    `TestClient` specifically to avoid mixing event loops/Prisma
    connections (see module docstring); instantiating a second app on top
    of that reintroduces exactly that deadlock. A same-named fixture
    defined directly in a test module overrides one from a parent conftest
    for tests in that module, so this no-op replaces it here. Each test in
    this module patches `enqueue_execution` itself where needed.
    """
    return None


_MINIMAL: dict[str, Any] = {
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 100, "y": 0}, "data": {"label": "E"}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "e"}],
}


def test_ws_two_user_authz(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connects to A's execution's WS and gets a snapshot; B's attempt on
    A's execution closes with 4403."""
    import os

    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    app = create_app()

    # This file deliberately builds its own app/TestClient (see module
    # docstring) to stay fully-sync end-to-end and avoid a cross-event-loop
    # Prisma deadlock. The shared tests/integration/conftest.py autouse
    # fixture that fakes enqueue_execution captures a *different* app
    # instance (the async `app` fixture from tests/conftest.py) via closure,
    # so using it here would reintroduce exactly the cross-loop deadlock
    # this file's architecture exists to avoid. A nested TestClient.post()
    # call is also unsafe here: the /executions handler that calls
    # enqueue_execution already runs on TestClient's background portal
    # thread/event loop, so a synchronous re-entrant call through the same
    # portal would deadlock waiting for a thread that's blocked on us.
    # Instead, call the claim-and-run route function directly in-process
    # (same coroutine, same event loop, same Prisma client off
    # app.state.db) bypassing FastAPI's HTTP/DI layer entirely.
    import types
    from typing import Literal

    from src.api.internal import ClaimAndRunRequest, claim_and_run

    async def _fake_enqueue(
        execution_id: str, *, kind: Literal["run", "resume"], db: object = None
    ) -> None:
        db = app.state.db
        dummy_request = types.SimpleNamespace(client=None, app=app)
        await claim_and_run(
            payload=ClaimAndRunRequest(executionId=execution_id, kind=kind),
            request=dummy_request,  # type: ignore[arg-type]
            db=db,
            _oidc=None,
        )

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)
    a_email = f"ws-a-{secrets.token_hex(6)}@example.com"
    b_email = f"ws-b-{secrets.token_hex(6)}@example.com"
    password = "correct-horse-battery-staple"
    wf_id: str | None = None
    user_a_id: str | None = None
    user_b_id: str | None = None

    with TestClient(app) as c:
        try:
            r = c.post(
                "/auth/register",
                json={"email": a_email, "password": password, "displayName": "A"},
            )
            assert r.status_code == 201, r.text
            token_a = r.json()["accessToken"]
            user_a_id = r.json()["id"]

            r = c.post(
                "/auth/register",
                json={"email": b_email, "password": password, "displayName": "B"},
            )
            assert r.status_code == 201, r.text
            token_b = r.json()["accessToken"]
            user_b_id = r.json()["id"]

            headers_a = {"Authorization": f"Bearer {token_a}"}
            r = c.post("/workflows", json={"name": "A wf", **_MINIMAL}, headers=headers_a)
            assert r.status_code == 201, r.text
            wf_id = r.json()["id"]

            r = c.post(
                "/executions",
                json={"workflowId": wf_id, "input": {}},
                headers=headers_a,
            )
            assert r.status_code in (201, 202), r.text
            exec_id = r.json()["id"]

            # A can connect, receives a snapshot event
            with c.websocket_connect(
                f"/executions/{exec_id}/ws", subprotocols=["bearer", token_a]
            ) as ws:
                msg = ws.receive_text()
                assert "workflow_" in msg

            # B's attempt on A's execution closes with 4403
            with (
                pytest.raises(WebSocketDisconnect) as exc_info,
                c.websocket_connect(
                    f"/executions/{exec_id}/ws", subprotocols=["bearer", token_b]
                ) as ws,
            ):
                ws.receive_text()
            assert exc_info.value.code == 4403
        finally:
            # No cleanup here — asyncio.run would close TestClient's lifespan loop.
            # Rows with random-suffixed emails leak on failure; acceptable for the
            # dev Neon instance used in integration tests.
            _ = (app, wf_id, user_a_id, user_b_id)
