"""Group 7: LangGraphExecutor run/resume with real graphs (via the adapter)."""

from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    ISO,
    Db,
    Member,
    Phase4aAdapter,
    ago,
    approval_graph,
    chain,
    install_email_recorder,
    node,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _running_row(
    adapter: Phase4aAdapter,
    db: Db,
    wid: str,
    owner: Member,
    run_input: Any = "hello",
) -> str:
    """What claim-and-run leaves behind: a `running` row holding a lease."""
    eid = await adapter.start_execution(wid, owner.id, run_input)
    db.tracked_executions.append(eid)
    await db.set_execution(
        eid,
        status="running",
        lease_owner="worker-1",
        lease_expires_at=ago(minutes=-20),
        running_since=ago(seconds=3),
        delivery_attempts=1,
    )
    db.tracked_threads.append((await db.must(eid)).thread_id)
    return eid


async def test_start_to_end_completes_with_events_one_to_n(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await _running_row(adapter, db, wid, member, "hello")
    bystander_wf = await db.seed_workflow(member)
    bystander = await db.seed_execution(bystander_wf, member, status="running", lease_owner="x")
    before_bystander = db.snapshot(await db.must(bystander))

    before = db.snapshot(await db.must(eid))
    await adapter.executor_run(eid)

    row = await db.must(eid)
    changed = {k for k, v in before.items() if v != db.snapshot(row)[k]}
    assert changed == {
        "status",
        "output",
        "variables",
        "node_results",
        "completed_at",
        "lease_owner",
        "lease_expires_at",
        "running_since",
    }, changed
    assert row.status == "completed" and row.error is None and row.failure_code is None
    assert row.output == "hello"
    assert row.variables.get("lastOutput") == "hello"
    assert set(row.node_results) == {"s", "e"}
    assert row.completed_at is not None and row.completed_at > ago(minutes=1)
    assert row.lease_owner is None and row.lease_expires_at is None and row.running_since is None
    assert row.delivery_attempts == 1 and row.input == "hello"

    events = await db.events(eid)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    types = [e.type for e in events]
    assert types[0] == "workflow_started" and types[-1] == "workflow_completed"
    assert events[0].payload == {"status": "running"}
    assert events[-1].payload == {"status": "completed"}
    assert types.count("workflow_started") == 1 and types.count("workflow_completed") == 1
    assert types == [
        "workflow_started",
        "node_started",
        "node_completed",
        "node_started",
        "node_completed",
        "workflow_completed",
    ]
    assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_failing_node_marks_failed_with_execution_error(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    nodes, edges = chain(node("t", "transform", transformScript="1 / 0"))
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    eid = await _running_row(adapter, db, wid, member)
    before = db.snapshot(await db.must(eid))
    await adapter.executor_run(eid)

    row = await db.must(eid)
    changed = {k for k, v in before.items() if v != db.snapshot(row)[k]}
    assert changed == {
        "status",
        "error",
        "failure_code",
        "completed_at",
        "lease_owner",
        "lease_expires_at",
        "running_since",
    }, changed
    assert row.status == "failed" and row.failure_code == "execution_error"
    assert row.error is not None and ": " in row.error
    assert row.output is None
    assert row.completed_at is not None
    assert row.lease_owner is None and row.lease_expires_at is None and row.running_since is None
    events = await db.events(eid)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[0].type == "workflow_started"
    assert events[-1].type == "workflow_completed" and events[-1].payload == {"status": "failed"}
    assert [e.type for e in events].count("workflow_completed") == 1


async def test_user_approval_pauses_with_pending_markers(
    adapter: Phase4aAdapter, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    emails = install_email_recorder(monkeypatch)
    nodes, edges = approval_graph()
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    eid = await _running_row(adapter, db, wid, member)
    before = db.snapshot(await db.must(eid))
    await adapter.executor_run(eid)

    row = await db.must(eid)
    changed = {k for k, v in before.items() if v != db.snapshot(row)[k]}
    assert changed == {"status", "variables", "lease_owner", "lease_expires_at", "running_since"}
    assert row.status == "waiting_approval"
    assert row.completed_at is None and row.error is None and row.failure_code is None
    assert row.lease_owner is None and row.lease_expires_at is None and row.running_since is None
    assert row.variables["_pending_approval_node"] == "ua"
    assert row.variables["_pending_approval_prompt"] == "Please approve"
    assert ISO.match(row.variables["_pending_approval_since"].replace("+00:00", "Z")) or ISO.match(
        row.variables["_pending_approval_since"]
    )
    assert row.output is None
    events = await db.events(eid)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[-1].type == "approval_required"
    assert events[-1].payload == {
        "node_id": "ua",
        "prompt": "Please approve",
        "status": "waiting_approval",
    }
    assert "workflow_completed" not in [e.type for e in events]
    assert len(emails) == 1
    assert emails[0]["execution_id"] == eid and emails[0]["node_id"] == "ua"
    assert emails[0]["approver_email"] == "approver@example.com"
    assert emails[0]["pending_since"] == row.variables["_pending_approval_since"]


async def test_executor_resume_after_pause_completes_and_continues_event_seq(
    adapter: Phase4aAdapter, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_email_recorder(monkeypatch)
    nodes, edges = approval_graph()
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    eid = await _running_row(adapter, db, wid, member)
    await adapter.executor_run(eid)
    paused_events = await db.event_seqs(eid)
    paused = await db.must(eid)
    assert paused.status == "waiting_approval"

    # What the (4b) resume route leaves behind: running, no lease, decision stamped.
    await db.set_execution(
        eid,
        status="running",
        variables={**paused.variables, "_resume_decision": "approved"},
        running_since=ago(seconds=1),
    )
    await adapter.executor_resume(eid, "approved")

    row = await db.must(eid)
    assert row.status == "completed" and row.completed_at is not None
    assert row.variables["result"] == "approved"
    seqs = await db.event_seqs(eid)
    assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) > len(paused_events)
    types = await db.event_types(eid)
    assert types[len(paused_events)] == "workflow_started"  # the resume's own start event
    assert types[-1] == "workflow_completed"


@pytest.mark.parametrize(
    "status", ["queued", "completed", "failed", "canceled", "waiting_approval"]
)
async def test_run_on_a_row_that_is_not_running_changes_nothing(
    adapter: Phase4aAdapter, member: Member, db: Db, status: str
) -> None:
    wid = await db.seed_workflow(member)
    eid = await db.seed_execution(wid, member, status=status, error="keep", input="x")
    before = db.snapshot(await db.must(eid))
    await adapter.executor_run(eid)
    assert db.snapshot(await db.must(eid)) == before
    assert await db.events(eid) == []


async def test_run_on_missing_execution_returns_quietly(adapter: Phase4aAdapter, db: Db) -> None:
    await adapter.executor_run("no-such-execution")
    assert await db.events("no-such-execution") == []


async def test_http_node_that_cancels_the_row_leaves_it_canceled(
    adapter: Phase4aAdapter, member: Member, db: Db, httpx_mock: HTTPXMock
) -> None:
    nodes, edges = chain(node("h", "http", httpUrl="https://93.184.216.34/x", httpMethod="GET"))
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    eid = await _running_row(adapter, db, wid, member)
    state: dict[str, Any] = {}

    async def _cancel_during_call(request: httpx.Request) -> httpx.Response:
        await db.set_execution(
            eid,
            status="canceled",
            error="Execution canceled by user request.",
            completed_at=ago(seconds=1),
            running_since=None,
            lease_owner=None,
            lease_expires_at=None,
        )
        state["canceled"] = db.snapshot(await db.must(eid))
        return httpx.Response(200, json={"ok": True})

    httpx_mock.add_callback(_cancel_during_call, url="https://93.184.216.34/x")
    await adapter.executor_run(eid)

    row = await db.must(eid)
    assert row.status == "canceled", (row.status, row.error)
    assert db.snapshot(row) == state["canceled"]  # not clobbered by completion or failure
    types = await db.event_types(eid)
    assert "workflow_completed" not in types
    assert types == [
        "workflow_started",
        "node_started",
        "node_completed",
        "node_started",
    ], types  # the cancel lands inside node h: no node_completed for h, nothing after
    assert await db.event_seqs(eid) == [1, 2, 3, 4]


# --------------------------------------------------------------------------- guarded transitions


async def _cancel(db: Db, eid: str) -> dict[str, Any]:
    await db.set_execution(
        eid,
        status="canceled",
        error="Execution canceled by user request.",
        completed_at=ago(seconds=1),
        running_since=None,
        lease_owner=None,
        lease_expires_at=None,
    )
    return db.snapshot(await db.must(eid))


async def test_pause_vs_cancel_cancel_wins_no_email_and_no_event(
    adapter: Phase4aAdapter, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    emails = install_email_recorder(monkeypatch)
    nodes, edges = approval_graph()
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    eid = await _running_row(adapter, db, wid, member)
    seen: list[str] = []
    state: dict[str, Any] = {}

    async def _hook(kind: str) -> None:
        seen.append(kind)
        state["canceled"] = await _cancel(db, eid)

    await adapter.executor_run(eid, before_transition=_hook)
    assert seen == ["waiting_approval"]
    assert db.snapshot(await db.must(eid)) == state["canceled"]
    assert emails == []
    types = await db.event_types(eid)
    assert "approval_required" not in types and "workflow_completed" not in types


async def test_completion_vs_cancel_cancel_wins_no_completed_event(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await _running_row(adapter, db, wid, member)
    seen: list[str] = []
    state: dict[str, Any] = {}

    async def _hook(kind: str) -> None:
        seen.append(kind)
        state["canceled"] = await _cancel(db, eid)

    await adapter.executor_run(eid, before_transition=_hook)
    assert seen == ["completed"]
    assert db.snapshot(await db.must(eid)) == state["canceled"]
    assert "workflow_completed" not in await db.event_types(eid)


async def test_failure_vs_cancel_cancel_wins_no_completed_event(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    nodes, edges = chain(node("t", "transform", transformScript="1 / 0"))
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    eid = await _running_row(adapter, db, wid, member)
    seen: list[str] = []
    state: dict[str, Any] = {}

    async def _hook(kind: str) -> None:
        seen.append(kind)
        state["canceled"] = await _cancel(db, eid)

    await adapter.executor_run(eid, before_transition=_hook)
    assert seen == ["failed"]
    assert db.snapshot(await db.must(eid)) == state["canceled"]
    assert "workflow_completed" not in await db.event_types(eid)


async def test_completion_vs_lease_sweep_sweep_wins_no_completed_event(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await _running_row(adapter, db, wid, member)
    state: dict[str, Any] = {}

    async def _hook(kind: str) -> None:
        assert kind == "completed"
        await db.set_execution(eid, lease_expires_at=ago(seconds=30))
        _scanned, marked, errored = await adapter.sweep_leases(now=ago(seconds=0))
        assert marked >= 1 and errored == 0
        state["swept"] = db.snapshot(await db.must(eid))

    await adapter.executor_run(eid, before_transition=_hook)
    row = await db.must(eid)
    assert row.status == "failed" and row.failure_code == "lease_expired"
    assert db.snapshot(row) == state["swept"]
    assert row.output is None
    assert "workflow_completed" not in await db.event_types(eid)


async def test_event_row_is_committed_before_its_notify_is_sent(
    adapter: Phase4aAdapter, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7: LISTEN/NOTIFY stays on asyncpg in 4a; a notify must never precede its row."""
    wid = await db.seed_workflow(member)
    eid = await _running_row(adapter, db, wid, member)
    notified: list[tuple[str, int, list[int]]] = []

    async def _notify(execution_id: str, *, seq: int) -> None:
        notified.append((execution_id, seq, await db.event_seqs(execution_id)))  # separate session

    monkeypatch.setattr("src.engine.langgraph_executor.notify_execution_event", _notify)
    monkeypatch.setattr("src.engine.events_wrapper.notify_execution_event", _notify)
    await adapter.executor_run(eid)
    assert [n[1] for n in notified] == [1, 2, 3, 4, 5, 6]
    for _eid, seq, visible in notified:
        assert _eid == eid and seq in visible, (seq, visible)
