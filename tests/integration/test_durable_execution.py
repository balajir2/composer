"""Integration tests for durable execution attempt expiry, terminal races, and
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

from prisma import Base64, Json  # pyright: ignore[reportAttributeAccessIssue]

pytestmark = pytest.mark.integration
NEEDS_LEGACY_PRISMA = (
    True  # tests/conftest.py gives this module a test-only Prisma client (phase 6 removes it)
)


def _terminal_race_graph(outcome: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
        {"id": "e", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E"}},
    ]
    edges: list[dict[str, Any]] = [{"id": "se", "source": "s", "target": "e"}]
    if outcome != "completed":
        nodes.append(
            {
                "id": "middle",
                "type": "transform" if outcome == "failed" else "user-approval",
                "position": {"x": 1, "y": 0},
                "data": (
                    {"label": "Fail", "transformScript": "1 / 0"}
                    if outcome == "failed"
                    else {
                        "label": "Approve",
                        "approvalMessage": "Approve this race test",
                        "approverEmail": "race@example.com",
                    }
                ),
            }
        )
        edges = [
            {"id": "sm", "source": "s", "target": "middle"},
            {"id": "me", "source": "middle", "target": "e"},
        ]
        if outcome == "waiting_approval":
            edges[1]["branch"] = "approved"
            nodes.append(
                {"id": "r", "type": "end", "position": {"x": 2, "y": 1}, "data": {"label": "R"}}
            )
            edges.append({"id": "mr", "source": "middle", "target": "r", "branch": "rejected"})
    return nodes, edges


@pytest.mark.parametrize("outcome", ["completed", "failed", "waiting_approval"])
@pytest.mark.parametrize("first_writer", ["cancel", "executor", "simultaneous"])
async def test_running_cancel_competes_with_executor_transition_in_postgres(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, outcome: str, first_writer: str
) -> None:
    """Removing a running-state CAS or its event/email guard must fail this test.

    Both operations compete from running. Public cancellation also accepts an
    already-paused row, so running -> waiting_approval -> canceled is a valid
    *sequential* pair of transitions, covered separately below.
    """
    from src.engine.langgraph_executor import LangGraphExecutor

    db: Any = app.state.db
    nodes, edges = _terminal_race_graph(outcome)
    workflow = await db.workflow.create(
        data={
            "userId": "dev",
            "name": "itest-terminal-race",
            "nodes": Json(nodes),
            "edges": Json(edges),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "userId": "dev",
            "status": "running",
            "threadId": f"itest-terminal-race-{workflow.id}",
            "nodeResults": Json({}),
            "variables": Json({}),
            "runningSince": datetime.now(UTC),
            "leaseOwner": "race-worker",
            "leaseExpiresAt": datetime.now(UTC) + timedelta(minutes=20),
        }
    )
    try:
        executor = LangGraphExecutor(
            sessionmaker=app.state.db_sessionmaker,
            checkpointer=app.state.checkpointer,
            event_bus=app.state.event_bus,
        )
        method_name = {
            "completed": "_mark_completed",
            "failed": "_mark_failed",
            "waiting_approval": "_mark_waiting_approval",
        }[outcome]
        transition = getattr(executor, method_name)
        executor_ready = asyncio.Event()
        start_updates = asyncio.Event()
        first_committed = asyncio.Event()
        executor_results: list[bool] = []
        sent_emails: list[dict[str, Any]] = []

        async def _competing_transition(*args: Any, **kwargs: Any) -> bool:
            # Control scheduling only. The original executor's Prisma update
            # and the cancel transaction both execute their real SQL predicates.
            executor_ready.set()
            await start_updates.wait()
            if first_writer == "cancel":
                await first_committed.wait()
            won: bool = await transition(*args, **kwargs)
            executor_results.append(won)
            if first_writer == "executor":
                first_committed.set()
            return won

        async def _cancel_running() -> int:
            await executor_ready.wait()
            start_updates.set()
            if first_writer == "executor":
                await first_committed.wait()
            async with db.tx() as tx:
                updated: int = await tx.workflowexecution.update_many(
                    where={"id": execution.id, "status": "running"},
                    data={
                        "status": "canceled",
                        "completedAt": datetime.now(UTC),
                        "runningSince": None,
                        "leaseOwner": None,
                        "leaseExpiresAt": None,
                    },
                )
            if first_writer == "cancel":
                first_committed.set()
            return updated

        async def _deliver_email(**kwargs: Any) -> None:
            # Only external delivery is replaced; pause persistence and the
            # executor's decision to send an approval email remain real.
            sent_emails.append(kwargs)

        monkeypatch.setattr(executor, method_name, _competing_transition)
        monkeypatch.setattr("src.engine.langgraph_executor.send_approval_email", _deliver_email)
        _, cancel_count = await asyncio.wait_for(
            asyncio.gather(executor.run(execution.id), _cancel_running()), timeout=30
        )

        assert len(executor_results) == 1
        executor_won = executor_results[0]
        assert cancel_count in (0, 1)
        assert int(executor_won) + cancel_count == 1
        if first_writer != "simultaneous":
            assert executor_won == (first_writer == "executor")
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == (outcome if executor_won else "canceled")
        assert refreshed.runningSince is None
        assert refreshed.leaseOwner is None
        assert refreshed.leaseExpiresAt is None
        assert refreshed.failureCode == (
            "execution_error" if executor_won and outcome == "failed" else None
        )

        events: list[Any] = await db.executionevent.find_many(where={"executionId": execution.id})
        outcome_events = [
            e for e in events if e.type in {"workflow_completed", "approval_required"}
        ]
        assert len(outcome_events) == int(executor_won)
        if executor_won:
            assert outcome_events[0].type == (
                "approval_required" if outcome == "waiting_approval" else "workflow_completed"
            )
            assert outcome_events[0].payload["status"] == outcome
        assert len(sent_emails) == int(executor_won and outcome == "waiting_approval")
        if sent_emails:
            assert sent_emails[0]["execution_id"] == execution.id
            assert sent_emails[0]["approver_email"] == "race@example.com"
    finally:
        await db.langgraphcheckpointwrite.delete_many(where={"threadId": execution.threadId})
        await db.langgraphcheckpoint.delete_many(where={"threadId": execution.threadId})
        await db.executionevent.delete_many(where={"executionId": execution.id})
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})


async def test_public_cancel_accepts_an_already_committed_approval_pause(
    app: FastAPI, client: AsyncClient
) -> None:
    """A pause winner remains cancellable in a subsequent public request."""
    from src.engine.langgraph_executor import LangGraphExecutor

    db: Any = app.state.db
    workflow = await db.workflow.create(
        data={
            "userId": "dev",
            "name": "itest-cancel-after-pause",
            "nodes": Json([]),
            "edges": Json([]),
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "userId": "dev",
            "status": "running",
            "threadId": f"itest-paused-{workflow.id}",
            "nodeResults": Json({}),
            "variables": Json({}),
        }
    )
    try:
        executor = LangGraphExecutor(
            sessionmaker=app.state.db_sessionmaker,
            checkpointer=app.state.checkpointer,
            event_bus=app.state.event_bus,
        )
        assert await executor._mark_waiting_approval(  # pyright: ignore[reportPrivateUsage]
            execution.id,
            {"node_id": "approval", "prompt": "Approve"},
            {},
            datetime.now(UTC).isoformat(),
        )
        response = await client.post(f"/executions/{execution.id}/cancel")
        assert response.status_code == 200, response.text
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == "canceled"
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})


async def test_expired_lease_fails_without_replaying_execution(
    client: AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
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

        # Deliver both task kinds before maintenance. Neither may steal the
        # dead worker's expired ownership or increment delivery attempts.
        for kind in ("run", "resume"):
            delivery = await client.post(
                "/internal/claim-and-run", json={"executionId": execution.id, "kind": kind}
            )
            assert delivery.status_code == 200, delivery.text
            assert delivery.json() == {"status": "already_claimed"}
        untouched = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert untouched is not None
        assert untouched.status == "running"
        assert untouched.leaseOwner == "dead-worker-1"
        assert untouched.deliveryAttempts == 1

        result = await sweep_expired_leases(app.state.db_sessionmaker)

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
            "nodeResults": Json({}),
            "variables": Json({}),
            "startedAt": now - timedelta(days=180),
            "runningSince": now - timedelta(seconds=10),
            "leaseOwner": "live-worker",
            "leaseExpiresAt": now + timedelta(minutes=20),
        }
    )
    try:
        await sweep_orphaned_running(app.state.db_sessionmaker, grace_seconds=120, now=now)
        await sweep_expired_leases(app.state.db_sessionmaker, now=now)

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
    app: FastAPI, sweep_kind: str, monkeypatch: pytest.MonkeyPatch
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
            "nodeResults": Json({}),
            "variables": Json({}),
            "runningSince": now - timedelta(minutes=31),
            "leaseOwner": "dead-worker" if sweep_kind == "lease" else None,
            "leaseExpiresAt": now - timedelta(seconds=30) if sweep_kind == "lease" else None,
        }
    )
    try:
        from src.db.repos import executions as executions_repo

        target = "list_expired_leases" if sweep_kind == "lease" else "list_orphaned_running"
        real_list = getattr(executions_repo, target)

        async def _select_then_claim(*args: Any, **kwargs: Any) -> list[Any]:
            # The seam controls timing only: selection, renewal, and the
            # sweeper's conditional update all execute against Postgres.
            rows = [r for r in await real_list(*args, **kwargs) if r.id == execution.id]
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

        monkeypatch.setattr(executions_repo, target, _select_then_claim)
        sweep_db = app.state.db_sessionmaker
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
            "nodeResults": Json({}),
            "variables": Json({}),
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

        # Original deliveries and unauthorized resume deliveries cannot
        # claim an approval pause, whose ownership has correctly been cleared.
        for kind in ("run", "resume"):
            duplicate = await client.post(
                "/internal/claim-and-run", json={"executionId": execution.id, "kind": kind}
            )
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json() == {"status": "already_claimed"}
        still_paused = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert still_paused is not None
        assert still_paused.status == "waiting_approval"
        assert still_paused.variables == paused.variables
        assert still_paused.deliveryAttempts == paused.deliveryAttempts

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
        mock_enqueue.assert_awaited_once_with(execution.id, kind="resume")

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

        await sweep_orphaned_running(app.state.db_sessionmaker, grace_seconds=120)
        await sweep_expired_leases(app.state.db_sessionmaker)
        awaiting_claim = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert awaiting_claim is not None
        assert awaiting_claim.status == "running"

        # A late original run task must not steal the authorized resume's
        # unowned running row and restart the graph from its original input.
        original = await client.post(
            "/internal/claim-and-run", json={"executionId": execution.id, "kind": "run"}
        )
        assert original.status_code == 200, original.text
        assert original.json() == {"status": "already_claimed"}

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
