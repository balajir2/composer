"""Pause in one app process, resume in a fresh one: the checkpoint survives a restart.

Real graphs (a user-approval node, real LangGraph, the app's own checkpointer) driven over HTTP.
"A restart" is a second ``create_app()`` with its own lifespan. The resumed run must be the SAME
run: same execution row, same thread, state carried by the checkpoint, nodes before the pause
never re-executed.

The one test named ``..._today_...`` pins behaviour phase 5 Task 7 changes on purpose (a resume
that finds no checkpoint fails the run with ``checkpoint_missing`` instead of restarting the graph
from scratch). It is the only test in this package a later task may change.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    Member,
    jbody,
)
from tests.integration.sqla_phase4b.conftest import install_resume_enqueue, resume
from tests.integration.sqla_phase5.conftest import (
    RunningApp,
    checkpoint_row_count,
    fan_out_graph,
    running_app,
    start_paused,
    two_approval_graph,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _event_types(db: Db, eid: str) -> list[tuple[str, Any]]:
    return [(e.type, e.payload.get("nodeId")) for e in await db.events(eid)]


async def _history_ids(a: RunningApp, thread: str) -> list[str]:
    cfg: Any = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    return [
        t.config["configurable"]["checkpoint_id"] async for t in a.app.state.checkpointer.alist(cfg)
    ]


async def _assert_one_linear_chain(a: RunningApp, thread: str) -> None:
    """Newest-first history that forms ONE chain: exactly one root, every other entry's parent is
    the next entry. A resume that raced or restarted would fork it."""
    cfg: Any = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    history = [t async for t in a.app.state.checkpointer.alist(cfg)]
    ids = [t.config["configurable"]["checkpoint_id"] for t in history]
    assert len(set(ids)) == len(ids)
    assert history[-1].parent_config is None
    for newer, older_id in zip(history[:-1], ids[1:], strict=True):
        assert newer.parent_config is not None
        assert newer.parent_config["configurable"]["checkpoint_id"] == older_id


@pytest.mark.parametrize(
    ("decision", "result"), [("approved", "approved"), ("rejected", "rejected")]
)
async def test_resume_after_restart_continues_the_same_run(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch, decision: str, result: str
) -> None:
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(a.client, db, member, monkeypatch)
        paused = await db.must(eid)
        cp_a = a.app.state.checkpointer
        head_before = (await _history_ids(a, thread))[0]
        rows_before = await checkpoint_row_count(a.sm, thread)
        events_before = await db.events(eid)
    assert [e.type for e in events_before][-1] == "approval_required"

    async with running_app(monkeypatch) as b:
        assert b.app.state.checkpointer is not cp_a  # a genuinely fresh saver read the rows
        r = await resume(b.client, member, eid, decision)
        assert r.status_code == 200, r.text
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        assert row.id == eid and row.thread_id == paused.thread_id == thread
        # the branch taken after the pause proves the decision reached the saved graph state
        assert row.variables["result"] == result
        assert "_pending_approval_node" not in row.variables
        # state captured before the pause came back out of the checkpoint, not out of the row
        assert row.variables["input"] == "x"
        # the checkpoint chain extended; nothing before the pause was rewritten
        assert await checkpoint_row_count(b.sm, thread) > rows_before
        history = await _history_ids(b, thread)
        assert head_before in history and history[0] != head_before
        await _assert_one_linear_chain(b, thread)

    # nothing that ran before the pause ran again: start and the approval node began exactly once
    events = await _event_types(db, eid)
    assert events.count(("node_started", "s")) == 1
    assert (
        events.count(("node_started", "ua")) == 2
    )  # the pause, then the re-entry that consumes the decision
    assert events[-1] == ("workflow_completed", None)
    assert [t for t, _ in events].count("workflow_started") == 2
    seqs = [e.seq for e in await db.events(eid)]
    assert seqs == list(range(1, len(seqs) + 1))


async def test_resume_after_restart_through_the_claim(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route only hands the row over; the claim (what Cloud Tasks delivers) runs the graph
    from the checkpoint on the fresh app."""
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(a.client, db, member, monkeypatch)

    async with running_app(monkeypatch) as b:
        rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
        r = await resume(b.client, member, eid, "approved")
        assert r.status_code == 200 and jbody(r)["status"] == "running", r.text
        assert rec.calls == [(eid, "resume")]
        assert (await db.must(eid)).status == "running"  # claimed by nobody yet

        claim = await b.client.post(
            "/internal/claim-and-run", json={"executionId": eid, "kind": "resume"}
        )
        assert claim.status_code == 200 and jbody(claim) == {"status": "completed"}
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        assert row.variables["result"] == "approved"
        await _assert_one_linear_chain(b, thread)
        # a redelivered task finds nothing to claim and does not run the graph again
        again = await b.client.post(
            "/internal/claim-and-run", json={"executionId": eid, "kind": "resume"}
        )
        assert jbody(again) == {"status": "already_claimed"}
    events = await _event_types(db, eid)
    assert events.count(("node_started", "s")) == 1
    assert [t for t, _ in events].count("workflow_completed") == 1


async def test_six_concurrent_claims_after_restart_resume_the_checkpoint_exactly_once(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(a.client, db, member, monkeypatch)

    async with running_app(monkeypatch) as b:
        install_resume_enqueue(monkeypatch, EnqueueRecorder())
        assert (await resume(b.client, member, eid, "approved")).status_code == 200
        responses = await asyncio.gather(
            *(
                b.client.post(
                    "/internal/claim-and-run", json={"executionId": eid, "kind": "resume"}
                )
                for _ in range(6)
            )
        )
        assert [r.status_code for r in responses] == [200] * 6
        outcomes = sorted(jbody(r)["status"] for r in responses)
        assert outcomes == ["already_claimed"] * 5 + ["completed"]
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        await _assert_one_linear_chain(b, thread)
    events = await _event_types(db, eid)
    assert [t for t, _ in events].count("workflow_completed") == 1
    assert events.count(("node_started", "ua")) == 2


async def test_six_concurrent_resume_requests_after_restart_run_the_graph_once(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(a.client, db, member, monkeypatch)

    async with running_app(monkeypatch) as b:
        responses = await asyncio.gather(
            *(resume(b.client, member, eid, "approved") for _ in range(6)), return_exceptions=True
        )
        codes = sorted(r.status_code if not isinstance(r, BaseException) else -1 for r in responses)
        assert codes == [200] + [409] * 5, codes
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        await _assert_one_linear_chain(b, thread)
    events = await _event_types(db, eid)
    assert [t for t, _ in events].count("workflow_completed") == 1
    assert events.count(("node_started", "ua")) == 2


async def test_chained_pause_survives_two_restarts(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(
            a.client, db, member, monkeypatch, graph=two_approval_graph
        )

    async with running_app(monkeypatch) as b:
        assert (await resume(b.client, member, eid, "approved")).status_code == 200
        row = await db.must(eid)
        assert row.status == "waiting_approval"
        assert row.variables["_pending_approval_node"] == "ua2"
        assert row.variables["first"] == "yes"
        mid_ids = await _history_ids(b, thread)

    async with running_app(monkeypatch) as c:
        assert (await resume(c.client, member, eid, "approved")).status_code == 200
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        assert (row.variables["first"], row.variables["second"]) == ("yes", "yes")
        assert row.variables["input"] == "x"
        history = await _history_ids(c, thread)
        assert set(mid_ids) <= set(history) and len(history) > len(mid_ids)
        await _assert_one_linear_chain(c, thread)
    events = await _event_types(db, eid)
    assert events.count(("node_started", "s")) == 1
    assert events.count(("node_started", "one")) == 1
    assert events.count(("node_started", "two")) == 1


async def test_fan_out_sibling_pending_writes_survive_restart_and_are_not_rerun(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start fans out to the approval node and a sibling that completes in the SAME super-step.
    The sibling's result lives only in the interrupted checkpoint's pending writes: after a
    restart + resume the sibling must not run again and its state must still be there."""
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(a.client, db, member, monkeypatch, graph=fan_out_graph)
        assert (await db.must(eid)).status == "waiting_approval"
        events = await _event_types(db, eid)
        assert events.count(("node_started", "side")) == 1
        head = await a.app.state.checkpointer.aget_tuple(
            {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
        )
        assert head is not None
        channels = {w[1] for w in head.pending_writes}
        assert "__interrupt__" in channels and len(head.pending_writes) > 1  # the sibling's too

    async with running_app(monkeypatch) as b:
        assert (await resume(b.client, member, eid, "approved")).status_code == 200
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        assert row.variables["side"] == "done" and row.variables["result"] == "approved"
        await _assert_one_linear_chain(b, thread)
    events = await _event_types(db, eid)
    assert events.count(("node_started", "side")) == 1
    assert events.count(("node_started", "s")) == 1


async def test_rejected_decision_after_restart_takes_the_rejected_branch_only(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_app(monkeypatch) as a:
        eid, _thread = await start_paused(a.client, db, member, monkeypatch)
    async with running_app(monkeypatch) as b:
        assert (await resume(b.client, member, eid, "rejected")).status_code == 200
    row = await db.must(eid)
    assert row.status == "completed" and row.variables["result"] == "rejected"
    started = [n for t, n in await _event_types(db, eid) if t == "node_started"]
    assert "no" in started and "ok" not in started


async def test_resume_without_a_checkpoint_restarts_the_graph_from_scratch_today(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TODAY: a resume that finds no checkpoint does not fail. The graph silently starts over on a
    fresh thread state with an EMPTY input, runs the start node again and pauses again at the
    same approval node (status ``waiting_approval``, no error, a second ``approval_required``).
    Phase 5 Task 7 (decision D2: fail closed) changes this to ``failed`` with
    ``failure_code="checkpoint_missing"``; the same rollback situation (a run that paused only in
    the other saver's tables) shows up exactly like this on the old revision. LATER TASK MAY
    CHANGE THIS TEST."""
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(a.client, db, member, monkeypatch)
        paused_events = len(await db.events(eid))
        async with a.sm() as s:
            for table in ("langgraph_checkpoint_writes", "langgraph_checkpoints"):
                await s.execute(text(f"DELETE FROM {table} WHERE thread_id = :t"), {"t": thread})
            await s.commit()
        assert await checkpoint_row_count(a.sm, thread) == 0

        r = await resume(a.client, member, eid, "approved")
        assert r.status_code == 200, r.text
        row = await db.must(eid)
        assert (row.status, row.error, row.failure_code) == ("waiting_approval", None, None)
        assert row.variables["_pending_approval_node"] == "ua"
        assert row.variables["input"] == ""  # the original input "x" was lost with the checkpoint
        assert "result" not in row.variables  # the decision was never applied
        assert await checkpoint_row_count(a.sm, thread) > 0  # a brand-new chain was written
    events = await _event_types(db, eid)
    assert len(events) > paused_events
    assert events.count(("node_started", "s")) == 2  # the start node ran a second time
    assert [t for t, _ in events].count("approval_required") == 2
