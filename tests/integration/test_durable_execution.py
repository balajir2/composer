"""Integration tests for durable execution lease expiration/recovery and
the single-claim guarantee (P1-2 explicit acceptance criterion:
"Integration tests simulate lease expiration and worker termination").

The unit tests added in Tasks 10-11 (tests/unit/maintenance/
test_execution_sweeper.py, tests/unit/api/test_internal.py) mock the DB
entirely; these tests exercise the real Postgres row-locking and
lease-column behavior the whole subsystem depends on.

Requires a real, reachable Postgres (TEST_DATABASE_URL) -- run via
`.venv/Scripts/python -m pytest -m integration tests/integration/test_durable_execution.py`.
Uses the shared `app`/`client` fixtures from tests/conftest.py (real
FastAPI lifespan, real Prisma client, real checkpointer/event bus wired
via `app.state`) rather than hand-rolling a second `Prisma()` connection
or a second `create_app()` call, matching every other file in this
directory (e.g. test_user_approval_approved.py's `db: Any = app.state.db`
pattern).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from prisma import Json  # pyright: ignore[reportAttributeAccessIssue]

pytestmark = pytest.mark.integration


async def test_expired_lease_is_reclaimed_and_marked_running_again(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates a worker that claimed an execution then died: the row's
    lease is set and already expired. `sweep_expired_leases` must clear
    the lease and re-enqueue (Task 11) — this test verifies the real-DB
    state transition; the Cloud Tasks call itself is monkeypatched since
    integration tests should not depend on real GCP credentials (the
    established pattern in tests/unit/maintenance/test_execution_sweeper.py,
    which patches the same `src.execution.cloud_tasks.enqueue_execution`
    target — `sweep_expired_leases` imports it lazily inside the function
    body, so patching the module attribute before the call is what makes
    the patch take effect)."""
    db: Any = app.state.db

    workflow = await db.workflow.create(
        data={
            "userId": "itest-user",
            "name": "itest-durable-exec",
            "nodes": Json([]),
            "edges": Json([]),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "status": "running",
            "threadId": f"itest-thread-{workflow.id}",
            "nodeResults": Json({}),
            "variables": Json({}),
            "leaseOwner": "dead-worker-1",
            "leaseExpiresAt": datetime.now(UTC) - timedelta(seconds=30),
            "deliveryAttempts": 1,
        }
    )

    try:
        from src.maintenance.execution_sweeper import sweep_expired_leases

        mock_enqueue = AsyncMock()
        monkeypatch.setattr("src.execution.cloud_tasks.enqueue_execution", mock_enqueue)

        result = await sweep_expired_leases(db, max_delivery_attempts=5)

        assert result.marked_failed == 0
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.leaseOwner is None
        assert refreshed.leaseExpiresAt is None
        mock_enqueue.assert_awaited_once_with(execution.id, kind="run")
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})


async def test_two_concurrent_claims_only_one_succeeds(client: AsyncClient, app: FastAPI) -> None:
    """Real end-to-end version of scripts/poc_persistence_row_lock.py's
    row-locking finding, exercised through the actual `claim_and_run` code
    path (via POST /internal/claim-and-run) rather than a standalone
    script — the single-claim guarantee this whole subsystem depends on.

    Deviates from the plan's illustrative snippet in one way worth
    documenting: it fires both requests through the shared async `client`
    fixture (httpx `AsyncClient` over `ASGITransport`, this repo's
    established integration-test convention — see every other file in
    this directory) rather than constructing a second, separate
    `TestClient`/`create_app()`. `app.state.db`/`checkpointer`/`event_bus`
    are already the real Postgres-backed instances here because the
    `app` fixture runs the full `create_app()` + lifespan (see
    tests/conftest.py), so there was nothing left to wire up by hand.

    NOTE on concurrency: `asyncio.gather` over two coroutines on one
    `AsyncClient` at least allows genuine interleaving at each `await`
    point (including the real network round-trips this test makes to
    Neon) — closer to true concurrency than a synchronous `TestClient`
    could ever offer, since a sync `TestClient` blocks the whole
    interpreter for the duration of the first call. Even so, there is no
    hard guarantee that both requests are simultaneously mid-flight
    inside `claim_and_run`'s `FOR UPDATE SKIP LOCKED` transaction at the
    same instant — that would require two genuinely separate worker
    processes, which is out of scope for a single pytest process. This is
    an accepted, documented limitation (see the Task 15 plan). It does
    NOT weaken the assertions below, though: regardless of whether the
    two requests race or serialize, `claim_and_run`'s lease predicate
    (`lease_expires_at IS NULL OR lease_expires_at < now()`) excludes an
    already-claimed, not-yet-expired row from a second claim just as
    surely as `FOR UPDATE SKIP LOCKED` excludes a concurrently-locked
    one — so a losing request always comes back `already_claimed` either
    way, and the assertions on the two response bodies (exactly one
    "completed", exactly one "already_claimed") still catch a real
    regression, e.g. someone removing `FOR UPDATE SKIP LOCKED` entirely
    would show up as two executions racing to "completed" or a crash.
    The row-lock guarantee itself is proven at the SQL level by
    scripts/poc_persistence_row_lock.py; this test proves the guarantee
    survives being wired into the real `claim_and_run` handler.
    """
    db: Any = app.state.db

    workflow = await db.workflow.create(
        data={
            "userId": "itest-user",
            "name": "itest-claim-race",
            "nodes": Json(
                [
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "e",
                        "type": "end",
                        "position": {"x": 1, "y": 0},
                        "data": {"label": "E"},
                    },
                ]
            ),
            "edges": Json([{"id": "e1", "source": "s", "target": "e"}]),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "status": "queued",
            "threadId": f"itest-race-{workflow.id}",
            "nodeResults": Json({}),
            "variables": Json({}),
        }
    )

    try:

        async def _claim() -> tuple[int, str]:
            resp = await client.post(
                "/internal/claim-and-run",
                json={"executionId": execution.id, "kind": "run"},
            )
            return resp.status_code, resp.json()["status"]

        results = await asyncio.gather(_claim(), _claim())
        assert all(code == 200 for code, _ in results), results

        statuses = [body_status for _, body_status in results]
        assert statuses.count("completed") == 1, statuses
        assert statuses.count("already_claimed") == 1, statuses

        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == "completed"
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})
