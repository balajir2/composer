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
        mock_enqueue.assert_awaited_once_with(execution.id, kind="run", db=db)
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


async def test_resume_clears_stale_lease_and_reclaim_succeeds(
    client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the P1-2 fast-follow bug end-to-end against real Postgres
    and proves the fix (2026-07-15 holistic branch-wide review finding):
    claim_and_run sets a real lease (leaseOwner/leaseExpiresAt) on claim;
    `_mark_waiting_approval` (src/engine/langgraph_executor.py) never
    clears it when the execution pauses for approval, so the stale lease
    survives the entire approval-pending window. Before the fix,
    `resume_execution`'s waiting_approval -> running transition also left
    the lease untouched, so the fresh Cloud Task delivery that follows
    (claim_and_run again, kind='resume') found zero claimable rows --
    `lease_expires_at` was still in the future -- and silently returned
    {"status": "already_claimed"} without ever calling
    `executor.resume()`. The row would sit at status='running' doing
    nothing until `sweep_expired_leases` eventually noticed (up to
    `execution_lease_seconds` later).

    This test drives the real claim -> pause -> resume -> reclaim round
    trip through the actual HTTP endpoints (not mocks) and asserts the
    SECOND claim-and-run call actually claims the row (does NOT return
    already_claimed) -- the only assertion that proves the lease was
    genuinely cleared, not merely that no exception was raised.
    """
    db: Any = app.state.db

    workflow = await db.workflow.create(
        data={
            "userId": "dev",
            "name": "itest-stale-lease-resume",
            "nodes": Json(
                [
                    {
                        "id": "s",
                        "type": "start",
                        "position": {"x": 0, "y": 0},
                        "data": {"label": "S"},
                    },
                    {
                        "id": "ua",
                        "type": "user-approval",
                        "position": {"x": 100, "y": 0},
                        "data": {"label": "UA", "approvalMessage": "Please approve"},
                    },
                    {
                        "id": "ok",
                        "type": "set-state",
                        "position": {"x": 200, "y": 0},
                        "data": {"label": "ok", "stateKey": "result", "stateValue": "approved"},
                    },
                    {
                        "id": "no",
                        "type": "set-state",
                        "position": {"x": 200, "y": 100},
                        "data": {"label": "no", "stateKey": "result", "stateValue": "rejected"},
                    },
                    {
                        "id": "e",
                        "type": "end",
                        "position": {"x": 300, "y": 0},
                        "data": {"label": "E"},
                    },
                ]
            ),
            "edges": Json(
                [
                    {"id": "e1", "source": "s", "target": "ua"},
                    {"id": "e2", "source": "ua", "target": "ok", "branch": "approved"},
                    {"id": "e3", "source": "ua", "target": "no", "branch": "rejected"},
                    {"id": "e4", "source": "ok", "target": "e"},
                    {"id": "e5", "source": "no", "target": "e"},
                ]
            ),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "userId": "dev",
            "status": "queued",
            "threadId": f"itest-stale-lease-{workflow.id}",
            "nodeResults": Json({}),
            "variables": Json({}),
        }
    )

    try:
        # Step 1: claim + run, synchronously, via the real endpoint --
        # sets a real lease (leaseOwner/leaseExpiresAt) as a side effect
        # of the claim UPDATE, then drives the graph to the user-approval
        # interrupt, which persists status='waiting_approval' via
        # `_mark_waiting_approval`. claim_and_run always responds
        # {"status": "completed"} once its own try/except doesn't raise
        # -- it does NOT reflect the execution's own resulting status, so
        # we check the DB row directly below rather than trust this
        # response body for that.
        claim1 = await client.post(
            "/internal/claim-and-run",
            json={"executionId": execution.id, "kind": "run"},
        )
        assert claim1.status_code == 200, claim1.text
        assert claim1.json()["status"] == "completed", claim1.json()

        paused = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert paused is not None
        assert paused.status == "waiting_approval", paused.status
        # The bug precondition, reproduced against real Postgres: the
        # lease from the original claim is still set on the row.
        assert paused.leaseOwner is not None, "lease must survive the pause (bug precondition)"
        assert paused.leaseExpiresAt is not None, "lease must survive the pause (bug precondition)"
        assert paused.leaseExpiresAt > datetime.now(UTC), (
            "lease must still be UNEXPIRED for this test to actually exercise the bug -- "
            "an already-expired lease would let claim_and_run's own "
            "`lease_expires_at < now()` clause reclaim the row regardless of the fix"
        )

        # Step 2: resolve the approval through the real, public resume
        # endpoint. Cloud Tasks itself is monkeypatched (no ADC
        # credentials in this test environment -- the established
        # pattern in tests/unit/api/test_executions_resume.py); this does
        # NOT touch the code under test, which is the `update_many` call
        # immediately above the (patched-out) enqueue call.
        mock_enqueue = AsyncMock()
        monkeypatch.setattr("src.api.executions.enqueue_execution", mock_enqueue)

        resume = await client.post(
            f"/executions/{execution.id}/resume",
            json={"decision": "approved"},
        )
        assert resume.status_code == 200, resume.text
        mock_enqueue.assert_awaited_once_with(execution.id, kind="resume", db=db)

        resumed_row = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert resumed_row is not None
        assert resumed_row.status == "running"
        # The actual fix, verified directly against real Postgres: the
        # stale lease from the original claim must be gone.
        assert resumed_row.leaseOwner is None, "fix did not clear leaseOwner"
        assert resumed_row.leaseExpiresAt is None, "fix did not clear leaseExpiresAt"

        # Step 3: the real proof -- a fresh Cloud Task delivery (what
        # `mock_enqueue` above stood in for) must be able to claim the
        # row NOW, not an hour from now. Before the fix, this call would
        # return {"status": "already_claimed"} because the stale,
        # still-unexpired lease from Step 1 excluded the row from
        # claim_and_run's claim predicate.
        claim2 = await client.post(
            "/internal/claim-and-run",
            json={"executionId": execution.id, "kind": "resume"},
        )
        assert claim2.status_code == 200, claim2.text
        assert claim2.json()["status"] != "already_claimed", (
            "reclaim failed -- the stale lease from the original claim "
            "was not cleared by resume_execution; this is exactly the "
            "bug the fix closes"
        )

        final_row = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert final_row is not None
        assert final_row.status == "completed", final_row.status
        final_vars: dict[str, Any] = final_row.variables or {}
        assert final_vars.get("result") == "approved"

        approvals = await db.approval.find_many(where={"executionId": execution.id})
        assert len(approvals) == 1
        assert approvals[0].decision == "approved"
    finally:
        thread_id = execution.threadId
        await db.langgraphcheckpointwrite.delete_many(where={"threadId": thread_id})
        await db.langgraphcheckpoint.delete_many(where={"threadId": thread_id})
        await db.executionevent.delete_many(where={"executionId": execution.id})
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})
