"""Dev-mode fallback: the REAL ``enqueue_execution`` (no Cloud Tasks service account) runs
claim-and-run as an in-process fire-and-forget task, with the app's sessionmaker and a
checkpointer, including pause and resume across an app restart.

Nothing here calls ``cloud_tasks._run_in_process`` directly (its signature changes in phase 5);
it is reached the way production code reaches it, through the routes' ``enqueue_execution``.
"""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    ExecApi,
    Member,
    approval_graph,
    install_email_recorder,
    jbody,
)
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import (
    checkpoint_row_count,
    running_app,
    track_thread,
    wait_for_status,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _wait_last_event(db: Db, eid: str, type_: str, timeout: float = 30.0) -> None:
    """The in-process task writes the status first and emits its event afterwards: wait for the
    event so the test (and its teardown) never races the task's last writes."""
    deadline = asyncio.get_running_loop().time() + timeout
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        events = await db.events(eid)
        last = events[-1].type if events else ""
        if last == type_:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"last event of {eid} is {last!r}, wanted {type_!r}")


async def _create(client: AsyncClient, db: Db, who: Member) -> str:
    nodes, edges = approval_graph()
    wid = await db.seed_workflow(who, nodes=nodes, edges=edges)
    r = await client.post(
        "/executions", json={"workflowId": wid, "input": "x"}, headers=who.headers
    )
    assert r.status_code == 202, r.text
    assert jbody(r)["status"] == "queued"  # enqueue is fire-and-forget: the response never waits
    eid = str(jbody(r)["id"])
    db.tracked_executions.append(eid)
    row = await db.must(eid)
    db.tracked_threads.append(row.thread_id)
    track_thread(row.thread_id)
    return eid


async def test_in_process_fallback_pauses_and_resumes(
    app: Any, exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.execution import cloud_tasks

    for site in (
        "src.api.executions.enqueue_execution",
        "src.api.approval_email.enqueue_execution",
        "src.api.run.enqueue_execution",
    ):  # undo the autouse synchronous driver: use the real function
        monkeypatch.setattr(site, cloud_tasks.enqueue_execution)
    install_email_recorder(monkeypatch)
    eid = await _create(exec_api.client, db, member)
    sm = app.state.db_sessionmaker
    assert await wait_for_status(sm, eid, {"waiting_approval", "failed"}, 30) == "waiting_approval"
    await _wait_last_event(db, eid, "approval_required")
    row = await db.must(eid)
    assert row.lease_owner is None and row.variables["_pending_approval_node"] == "ua"
    assert await checkpoint_row_count(sm, row.thread_id) > 0
    # the in-process task's checkpoints are the ones the app's own checkpointer reads
    cfg: Any = {"configurable": {"thread_id": row.thread_id, "checkpoint_ns": ""}}
    head = await app.state.checkpointer.aget_tuple(cfg)
    assert head is not None and any(w[1] == "__interrupt__" for w in head.pending_writes)

    assert (await resume(exec_api.client, member, eid, "approved")).status_code == 200
    assert await wait_for_status(sm, eid, {"completed", "failed"}, 30) == "completed"
    await _wait_last_event(db, eid, "workflow_completed")
    row = await db.must(eid)
    assert row.variables["result"] == "approved" and row.variables["input"] == "x"


async def test_in_process_pause_then_restart_then_in_process_resume(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_email_recorder(monkeypatch)
    async with running_app(monkeypatch, dev_mode_enqueue=True) as a:
        eid = await _create(a.client, db, member)
        assert await wait_for_status(a.sm, eid, {"waiting_approval", "failed"}, 30) == (
            "waiting_approval"
        )
        # the fire-and-forget task also emits the approval event after the status write
        await _wait_last_event(db, eid, "approval_required")
        thread = (await db.must(eid)).thread_id
        rows = await checkpoint_row_count(a.sm, thread)

    async with running_app(monkeypatch, dev_mode_enqueue=True) as b:
        assert (await resume(b.client, member, eid, "rejected")).status_code == 200
        assert await wait_for_status(b.sm, eid, {"completed", "failed"}, 30) == "completed"
        await _wait_last_event(db, eid, "workflow_completed")
        row = await db.must(eid)
        assert row.variables["result"] == "rejected" and row.variables["input"] == "x"
        assert await checkpoint_row_count(b.sm, thread) > rows
    events = [(e.type, e.payload.get("nodeId")) for e in await db.events(eid)]
    assert events.count(("node_started", "s")) == 1  # resumed from the checkpoint, not restarted


async def test_in_process_run_to_completion_without_a_pause(
    app: Any, exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.execution import cloud_tasks

    for site in (
        "src.api.executions.enqueue_execution",
        "src.api.approval_email.enqueue_execution",
        "src.api.run.enqueue_execution",
    ):
        monkeypatch.setattr(site, cloud_tasks.enqueue_execution)
    wid = await db.seed_workflow(member)  # start -> end
    r = await exec_api.create(member, wid, "hello")
    assert r.status_code == 202
    eid = str(jbody(r)["id"])
    db.tracked_executions.append(eid)
    row = await db.must(eid)
    db.tracked_threads.append(row.thread_id)
    track_thread(row.thread_id)
    sm = app.state.db_sessionmaker
    assert await wait_for_status(sm, eid, {"completed", "failed"}, 30) == "completed"
    await _wait_last_event(db, eid, "workflow_completed")
    assert (await db.must(eid)).output == "hello"
