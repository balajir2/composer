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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from prisma import Base64, Json  # pyright: ignore[reportAttributeAccessIssue]

pytestmark = pytest.mark.integration


async def test_expired_lease_fails_without_replaying_execution(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead worker's expired attempt fails without replaying side effects."""
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
            "runningSince": datetime.now(UTC) - timedelta(minutes=31),
            "deliveryAttempts": 1,
        }
    )

    try:
        from src.maintenance.execution_sweeper import sweep_expired_leases

        mock_enqueue = AsyncMock()
        monkeypatch.setattr("src.execution.cloud_tasks.enqueue_execution", mock_enqueue)

        result = await sweep_expired_leases(db)

        assert result.marked_failed >= 1
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.failureCode == "lease_expired"
        assert refreshed.leaseOwner is None
        assert refreshed.leaseExpiresAt is None
        assert refreshed.runningSince is None
        mock_enqueue.assert_not_awaited()
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})


async def test_months_old_execution_with_recent_attempt_and_live_lease_survives_sweeps(
    app: FastAPI,
) -> None:
    from src.maintenance.execution_sweeper import sweep_expired_leases, sweep_orphaned_running

    db: Any = app.state.db
    now = datetime.now(UTC)
    workflow = await db.workflow.create(
        data={
            "userId": "itest-user",
            "name": "itest-recent-attempt",
            "nodes": Json([]),
            "edges": Json([]),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "status": "running",
            "threadId": f"itest-recent-{workflow.id}",
            "startedAt": now - timedelta(days=180),
            "runningSince": now - timedelta(seconds=10),
            "leaseOwner": "live-worker",
            "leaseExpiresAt": now + timedelta(minutes=20),
        }
    )
    try:
        await sweep_orphaned_running(db, grace_seconds=120, now=now)
        await sweep_expired_leases(db, now=now)

        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == "running"
        assert refreshed.runningSince == execution.runningSince
        assert refreshed.leaseOwner == "live-worker"
        assert refreshed.leaseExpiresAt == execution.leaseExpiresAt
        assert refreshed.failureCode is None
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})


@pytest.mark.parametrize("sweep_kind", ["lease", "orphan"])
async def test_sweeper_stale_read_cannot_fail_attempt_claimed_after_selection(
    app: FastAPI, sweep_kind: str
) -> None:
    from src.maintenance.execution_sweeper import sweep_expired_leases, sweep_orphaned_running

    db: Any = app.state.db
    now = datetime.now(UTC)
    workflow = await db.workflow.create(
        data={
            "userId": "itest-user",
            "name": "itest-sweep-race",
            "nodes": Json([]),
            "edges": Json([]),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "status": "running",
            "threadId": f"itest-sweep-race-{workflow.id}",
            "runningSince": now - timedelta(minutes=31),
            "leaseOwner": "dead-worker" if sweep_kind == "lease" else None,
            "leaseExpiresAt": now - timedelta(seconds=30) if sweep_kind == "lease" else None,
        }
    )
    try:

        async def _select_then_claim(*, where: dict[str, Any]) -> list[Any]:
            # The seam controls timing only: selection, renewal, and the
            # sweeper's conditional update all execute against Postgres.
            rows: list[Any] = await db.workflowexecution.find_many(
                where={"id": execution.id, **where}
            )
            assert len(rows) == 1
            await db.workflowexecution.update(
                where={"id": execution.id},
                data={
                    "runningSince": now,
                    "leaseOwner": "renewed-worker",
                    "leaseExpiresAt": now + timedelta(minutes=20),
                },
            )
            return rows

        sweep_db = SimpleNamespace(
            workflowexecution=SimpleNamespace(
                find_many=_select_then_claim,
                update_many=db.workflowexecution.update_many,
            )
        )
        if sweep_kind == "lease":
            result = await sweep_expired_leases(sweep_db, now=now)
        else:
            result = await sweep_orphaned_running(sweep_db, grace_seconds=120, now=now)

        assert result.scanned == 1
        assert result.marked_failed == 0
        assert result.errored == 0
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == "running"
        assert refreshed.leaseOwner == "renewed-worker"
        assert refreshed.leaseExpiresAt > now
        assert refreshed.runningSince >= now - timedelta(milliseconds=1)
        assert refreshed.failureCode is None
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})


async def test_cancel_retains_checkpoint_and_pending_writes(
    client: AsyncClient, app: FastAPI
) -> None:
    db: Any = app.state.db
    now = datetime.now(UTC)
    workflow = await db.workflow.create(
        data={
            "userId": "dev",
            "name": "itest-cancel-retention",
            "nodes": Json([]),
            "edges": Json([]),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "userId": "dev",
            "status": "running",
            "threadId": f"itest-cancel-{workflow.id}",
            "runningSince": now,
            "leaseOwner": "active-worker",
            "leaseExpiresAt": now + timedelta(minutes=20),
        }
    )
    try:
        await db.langgraphcheckpoint.create(
            data={
                "threadId": execution.threadId,
                "checkpointId": "checkpoint-1",
                "checkpoint": Base64.encode(b"retained-checkpoint"),
                "metadata": Base64.encode(b"retained-metadata"),
            }
        )
        await db.langgraphcheckpointwrite.create(
            data={
                "threadId": execution.threadId,
                "checkpointId": "checkpoint-1",
                "taskId": "task-1",
                "idx": 0,
                "channel": "result",
                "value": Base64.encode(b"retained-write"),
            }
        )

        response = await client.post(f"/executions/{execution.id}/cancel")

        assert response.status_code == 200, response.text
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == "canceled"
        assert refreshed.runningSince is None
        assert refreshed.leaseOwner is None
        assert refreshed.leaseExpiresAt is None
        checkpoints = await db.langgraphcheckpoint.find_many(where={"threadId": execution.threadId})
        writes = await db.langgraphcheckpointwrite.find_many(where={"threadId": execution.threadId})
        assert len(checkpoints) == 1
        assert checkpoints[0].checkpoint.decode() == b"retained-checkpoint"
        assert len(writes) == 1
        assert writes[0].value.decode() == b"retained-write"
    finally:
        await db.langgraphcheckpointwrite.delete_many(where={"threadId": execution.threadId})
        await db.langgraphcheckpoint.delete_many(where={"threadId": execution.threadId})
        await db.executionevent.delete_many(where={"executionId": execution.id})
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
    """Pause releases ownership; resume starts a fresh, immediately claimable attempt."""
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
        assert paused.runningSince is None
        assert paused.leaseOwner is None
        assert paused.leaseExpiresAt is None

        # Legacy paused rows may still have a lease from an older worker.
        # Resume must also clear those fields in its guarded transition.
        await db.workflowexecution.update(
            where={"id": execution.id},
            data={
                "leaseOwner": "legacy-worker",
                "leaseExpiresAt": datetime.now(UTC) + timedelta(minutes=20),
                "startedAt": datetime.now(UTC) - timedelta(days=180),
            },
        )

        # Step 2: resolve the approval through the real, public resume
        # endpoint. Cloud Tasks itself is monkeypatched (no ADC
        # credentials in this test environment -- the established
        # pattern in tests/unit/api/test_executions_resume.py); this does
        # NOT touch the code under test, which is the `update_many` call
        # immediately above the (patched-out) enqueue call.
        mock_enqueue = AsyncMock()
        monkeypatch.setattr("src.api.executions.enqueue_execution", mock_enqueue)

        resumed_at = datetime.now(UTC)
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
        assert resumed_row.runningSince is not None
        assert resumed_row.runningSince >= resumed_at - timedelta(milliseconds=1)
        assert resumed_row.runningSince <= datetime.now(UTC)

        from src.maintenance.execution_sweeper import sweep_expired_leases, sweep_orphaned_running

        await sweep_orphaned_running(db, grace_seconds=120)
        await sweep_expired_leases(db)
        awaiting_claim = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert awaiting_claim is not None
        assert awaiting_claim.status == "running"

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
        assert final_row.runningSince is None
        assert final_row.leaseOwner is None
        assert final_row.leaseExpiresAt is None
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
