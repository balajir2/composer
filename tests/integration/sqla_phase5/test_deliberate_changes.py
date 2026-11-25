"""Behaviour phase 5 Task 7 changes on purpose (not part of the oracle).

Every test here must FAIL on the code before the cutover and pass after it:
- the app's checkpointer is the official ``AsyncPostgresSaver`` on its own psycopg pool, and a
  fresh run writes only the new tables;
- a run that paused on the OLD tables (an old instance, a rollback window) resumes after the
  cutover: copy-on-resume brings it forward and never touches the old rows;
- a resume that finds no checkpoint in either store FAILS the run (``checkpoint_missing``) instead
  of silently restarting the graph;
- the saver holds a pool connection only per saver call, never across a node.
"""

import asyncio
from collections.abc import Awaitable
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg_pool import AsyncConnectionPool
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.checkpoints.copy import copy_thread
from src.checkpoints.legacy import LegacyCheckpointSaver
from src.checkpoints.sync import bring_forward
from src.engine.graph_builder import build_graph
from src.engine.state import initial_state
from src.engine.workflow import Workflow
from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    approval_graph,
    chain,
    install_email_recorder,
    jbody,
    node,
)
from tests.integration.sqla_phase4b.conftest import iso_now, paused_variables, resume
from tests.integration.sqla_phase5.conftest import (
    CHECKPOINT_TABLES,
    checkpoint_row_counts,
    running_app,
    start_paused,
    track_thread,
    two_approval_graph,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

OLD = ("langgraph_checkpoints", "langgraph_checkpoint_writes")
NEW = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _only(counts: dict[str, int], tables: tuple[str, ...]) -> dict[str, int]:
    return {t: counts[t] for t in tables}


def _lg(thread: str) -> Any:
    return {"configurable": {"thread_id": thread}}


def _compile(graph: Any, saver: BaseCheckpointSaver[Any]) -> Any:
    nodes, edges = graph()
    wf = Workflow.model_validate({"id": "wf", "name": "n", "nodes": nodes, "edges": edges})
    return build_graph(wf, saver)


async def _newest_id(saver: BaseCheckpointSaver[Any], thread: str) -> str:
    tup = await saver.aget_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
    assert tup is not None
    return str((tup.config.get("configurable") or {})["checkpoint_id"])


async def _ids(saver: BaseCheckpointSaver[Any], thread: str) -> list[str]:
    cfg: Any = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    return [(t.config.get("configurable") or {})["checkpoint_id"] async for t in saver.alist(cfg)]


async def _seed_paused_on_old_tables(
    db: Db,
    who: Member,
    sm: async_sessionmaker[AsyncSession],
    graph: Any = approval_graph,
) -> tuple[str, str]:
    """A ``waiting_approval`` row whose checkpoints exist ONLY in the old tables (what an old
    revision leaves behind)."""
    nodes, edges = graph()
    wid = await db.seed_workflow(who, nodes=nodes, edges=edges)
    eid = await db.seed_execution(
        wid,
        who,
        status="waiting_approval",
        input="x",
        variables=paused_variables(iso_now()),
        delivery_attempts=1,
    )
    thread = (await db.must(eid)).thread_id
    track_thread(thread)
    legacy = LegacyCheckpointSaver(sm)
    await _compile(graph, legacy).ainvoke(initial_state("x"), config=_lg(thread))
    return eid, thread


async def test_resume_with_no_checkpoint_fails_closed(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(a.client, db, member, monkeypatch)
        before = await db.must(eid)
        async with a.sm() as s:
            for table in CHECKPOINT_TABLES:
                await s.execute(text(f"DELETE FROM {table} WHERE thread_id = :t"), {"t": thread})
            await s.commit()
        assert sum((await checkpoint_row_counts(a.sm, thread)).values()) == 0

        r = await resume(a.client, member, eid, "approved")
        assert r.status_code == 200, r.text
        row = await db.must(eid)
        assert row.status == "failed"
        assert row.failure_code == "checkpoint_missing"
        assert row.error is not None and eid in row.error
        assert "ok" not in row.node_results and "no" not in row.node_results
        assert row.node_results == before.node_results
        assert sum((await checkpoint_row_counts(a.sm, thread)).values()) == 0  # not restarted
    events = [(e.type, e.payload.get("nodeId")) for e in await db.events(eid)]
    assert events.count(("node_started", "s")) == 1  # the start node never ran again
    assert events[-1] == ("workflow_completed", None)


async def test_new_runs_write_only_the_new_tables(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with running_app(monkeypatch) as a:
        saver = a.app.state.checkpointer
        assert isinstance(saver, AsyncPostgresSaver)
        assert isinstance(saver.conn, AsyncConnectionPool)  # a pool, not one shared connection
        eid, thread = await start_paused(a.client, db, member, monkeypatch)
        counts = await checkpoint_row_counts(a.sm, thread)
        assert counts["checkpoints"] > 0
        assert _only(counts, OLD) == dict.fromkeys(OLD, 0)
        r = await resume(a.client, member, eid, "approved")
        assert r.status_code == 200, r.text
        assert (await db.must(eid)).status == "completed"
        after = await checkpoint_row_counts(a.sm, thread)
        assert after["checkpoints"] > counts["checkpoints"]
        assert _only(after, OLD) == dict.fromkeys(OLD, 0)


async def test_dev_mode_runs_write_only_the_new_tables(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process fallback (real ``enqueue_execution``) uses the registered saver."""
    install_email_recorder(monkeypatch)
    async with running_app(monkeypatch, dev_mode_enqueue=True) as a:
        assert isinstance(a.app.state.checkpointer, AsyncPostgresSaver)
        nodes, edges = approval_graph()
        wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
        r = await a.client.post(
            "/executions", json={"workflowId": wid, "input": "x"}, headers=member.headers
        )
        assert r.status_code == 202, r.text
        eid = str(jbody(r)["id"])
        db.tracked_executions.append(eid)
        thread = (await db.must(eid)).thread_id
        db.tracked_threads.append(thread)
        track_thread(thread)
        await _wait_status(db, eid, "waiting_approval")
        await _wait_last_event(db, eid, "approval_required")
        counts = await checkpoint_row_counts(a.sm, thread)
        assert counts["checkpoints"] > 0
        assert _only(counts, OLD) == dict.fromkeys(OLD, 0)

        assert (await resume(a.client, member, eid, "approved")).status_code == 200
        await _wait_status(db, eid, "completed")
        await _wait_last_event(db, eid, "workflow_completed")
        after = await checkpoint_row_counts(a.sm, thread)
        assert after["checkpoints"] > counts["checkpoints"]
        assert _only(after, OLD) == dict.fromkeys(OLD, 0)


async def _wait_status(db: Db, eid: str, status: str, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    row = await db.must(eid)
    while row.status != status and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
        row = await db.must(eid)
    assert row.status == status, (row.status, row.error)


async def _wait_last_event(db: Db, eid: str, type_: str, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        events = await db.events(eid)
        last = events[-1].type if events else ""
        if last == type_:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"last event of {eid} is {last!r}, wanted {type_!r}")


async def test_run_paused_on_the_old_tables_resumes_after_cutover(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_email_recorder(monkeypatch)
    async with running_app(monkeypatch) as a:
        eid, thread = await _seed_paused_on_old_tables(db, member, a.sm)
        old_before = _only(await checkpoint_row_counts(a.sm, thread), OLD)
        assert old_before["langgraph_checkpoints"] > 0
        assert _only(await checkpoint_row_counts(a.sm, thread), NEW) == dict.fromkeys(NEW, 0)
        old_ids = await _ids(LegacyCheckpointSaver(a.sm), thread)

        r = await resume(a.client, member, eid, "approved")
        assert r.status_code == 200, r.text
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        assert row.variables["result"] == "approved"

        after = await checkpoint_row_counts(a.sm, thread)
        assert _only(after, OLD) == old_before  # the old rows are history: untouched
        new_ids = await _ids(a.app.state.checkpointer, thread)
        assert set(old_ids) <= set(new_ids)  # the full copied history, ...
        assert len(new_ids) > len(old_ids)  # ... plus what the resume wrote
        assert after["checkpoints"] == len(new_ids)


async def test_new_tables_ahead_are_not_overwritten_by_the_old_ones(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pause on the new tables, copy the first pause back to the old tables, resume to the second
    pause (the new tables are now ahead), resume again: copy-on-resume must do nothing and the run
    continues from the new head."""
    async with running_app(monkeypatch) as a:
        eid, thread = await start_paused(
            a.client, db, member, monkeypatch, graph=two_approval_graph
        )
        saver = a.app.state.checkpointer
        legacy = LegacyCheckpointSaver(a.sm)
        await copy_thread(saver, legacy, thread, to="legacy", namespaces=[""])
        stale_head = await _newest_id(legacy, thread)

        assert (await resume(a.client, member, eid, "approved")).status_code == 200
        assert (await db.must(eid)).variables["_pending_approval_node"] == "ua2"
        new_head = await _newest_id(saver, thread)
        assert new_head > stale_head
        old_before = _only(await checkpoint_row_counts(a.sm, thread), OLD)

        assert await bring_forward(saver, a.sm, thread) is False
        assert (await resume(a.client, member, eid, "approved")).status_code == 200
        row = await db.must(eid)
        assert row.status == "completed", (row.status, row.error)
        assert (row.variables["first"], row.variables["second"]) == ("yes", "yes")
        assert new_head in await _ids(saver, thread)
        assert _only(await checkpoint_row_counts(a.sm, thread), OLD) == old_before
        assert await _newest_id(legacy, thread) == stale_head


async def test_saver_pool_serves_concurrent_runs(
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
) -> None:
    """Four runs at once on a pool of two, each parked 1.5 s inside an http node, with a 0.75 s
    checkout timeout: if a connection were held across a node, the third and fourth run would wait
    out the first two and raise ``PoolTimeout``. All complete, none fails.

    Two things keep CPU stalls out of the measurement: a first single run warms the code paths
    (first-use imports), and the http node's TLS context is built once (``httpx.AsyncClient()``
    builds one per request and blocks the event loop for 0.2-0.5 s on Windows, which would look
    like a held connection)."""
    import httpx._transports.default as httpx_transport  # pyright: ignore[reportPrivateUsage]

    tls_context = httpx.create_ssl_context()

    def _cached_context(*_args: Any, **_kwargs: Any) -> Any:
        return tls_context

    monkeypatch.setattr(httpx_transport, "create_ssl_context", _cached_context)
    monkeypatch.setenv("CHECKPOINTER_POOL_MAX_SIZE", "2")
    monkeypatch.setenv("CHECKPOINTER_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("CHECKPOINTER_POOL_TIMEOUT_SECONDS", "0.75")
    delay = 0.0

    async def _slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        return httpx.Response(200, json={"ok": True})

    httpx_mock.add_callback(_slow, url="https://93.184.216.34/slow", is_reusable=True)
    nodes, edges = chain(node("h", "http", httpUrl="https://93.184.216.34/slow", httpMethod="GET"))
    async with running_app(monkeypatch) as a:
        assert a.app.state.checkpointer.conn.max_size == 2
        wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
        eids = [await db.seed_execution(wid, member, status="queued") for _ in range(5)]
        for eid in eids:
            track_thread((await db.must(eid)).thread_id)

        def claim(eid: str) -> Awaitable[httpx.Response]:
            return a.client.post(
                "/internal/claim-and-run", json={"executionId": eid, "kind": "run"}
            )

        warm = await claim(eids[0])
        assert warm.status_code == 200 and (await db.must(eids[0])).status == "completed"
        delay = 1.5
        responses = await asyncio.gather(*(claim(e) for e in eids[1:]))
        assert [r.status_code for r in responses] == [200] * 4
        for eid in eids:
            row = await db.must(eid)
            assert row.status == "completed", (row.status, row.error, row.failure_code)


async def test_resume_command_still_reaches_a_copied_graph_state(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard for the helper above: a graph paused on the old saver resumes on the official one."""
    async with running_app(monkeypatch) as a:
        _eid, thread = await _seed_paused_on_old_tables(db, member, a.sm)
        legacy = LegacyCheckpointSaver(a.sm)
        saver = a.app.state.checkpointer
        assert await bring_forward(saver, a.sm, thread) is True
        assert await bring_forward(saver, a.sm, thread) is False  # now level: nothing to do
        final = await _compile(approval_graph, saver).ainvoke(
            Command(resume="approved"), config=_lg(thread)
        )
        assert final["variables"]["result"] == "approved"
        assert await _newest_id(legacy, thread) < await _newest_id(saver, thread)
