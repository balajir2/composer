"""Behaviour phase 5 Task 7 changes on purpose (not part of the oracle).

Every test here must FAIL on the code before the cutover and pass after it:
- the app's checkpointer is the official ``AsyncPostgresSaver`` on its own psycopg pool, and a
  fresh run writes only the new tables;
- the old Prisma checkpoint tables no longer exist (Alembic 0006), so nothing reads or writes them;
- a resume that finds no checkpoint FAILS the run (``checkpoint_missing``) instead
  of silently restarting the graph;
- the saver holds a pool connection only per saver call, never across a node.
"""

import asyncio
from collections.abc import Awaitable
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import (
    CHECKPOINT_TABLES,
    checkpoint_row_counts,
    running_app,
    start_paused,
    track_thread,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _old_tables_are_gone(sm: async_sessionmaker[AsyncSession]) -> bool:
    async with sm() as s:
        found = (
            await s.execute(
                text(
                    "SELECT to_regclass('langgraph_checkpoints'),"
                    " to_regclass('langgraph_checkpoint_writes')"
                )
            )
        ).one()
    return found == (None, None)


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
        assert await _old_tables_are_gone(a.sm)
        r = await resume(a.client, member, eid, "approved")
        assert r.status_code == 200, r.text
        assert (await db.must(eid)).status == "completed"
        after = await checkpoint_row_counts(a.sm, thread)
        assert after["checkpoints"] > counts["checkpoints"]
        assert await _old_tables_are_gone(a.sm)


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
        assert await _old_tables_are_gone(a.sm)

        assert (await resume(a.client, member, eid, "approved")).status_code == 200
        await _wait_status(db, eid, "completed")
        await _wait_last_event(db, eid, "workflow_completed")
        after = await checkpoint_row_counts(a.sm, thread)
        assert after["checkpoints"] > counts["checkpoints"]
        assert await _old_tables_are_gone(a.sm)


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
