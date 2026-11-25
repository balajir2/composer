"""Fixtures for the SQLAlchemy phase 5 behaviour oracle (checkpointer and notifications).

Written against the post-phase-4 code (PrismaCheckpointSaver, asyncpg LISTEN/NOTIFY). These
tests must keep passing UNCHANGED after the official AsyncPostgresSaver and psycopg
notifications replace them, except the tests named ``..._today_...``: they pin behaviour the plan
deliberately changes (listed in each module docstring). There is no adapter: every seam used here
keeps its interface: HTTP routes, ``create_app()`` + lifespan, ``app.state.checkpointer`` (a
``BaseCheckpointSaver``), ``app.state.event_bus``, ``app.state.db_sessionmaker`` and
``notify_execution_event(execution_id, *, seq)``.

"A restart" is a second ``create_app()`` with its own lifespan: a fresh checkpointer (fresh pool,
no in-memory state) reading what the first one wrote.

Checkpoint rows are counted with raw SQL over whichever of the five checkpoint tables exist (the
two old Prisma tables, the three official-saver tables), so the same helper is right before and
after the new tables appear. Every row of every tracked thread is removed in teardown.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.main import create_app
from tests._rate_limit_reset import reset_rate_limit_buckets
from tests.integration.sqla_phase4a.conftest import (
    Db,
    ExecApi,
    Member,
    app,
    approval_graph,
    db,
    edge,
    end_node,
    exec_api,
    install_email_recorder,
    jbody,
    make_user,
    member,
    node,
    phase4a_env,
    second_member,
    sessionmaker_,
    start_node,
)

# Fixtures re-exported from the 4a oracle (listing them here marks the imports as used).
__all__ = [
    "Db",
    "ExecApi",
    "Member",
    "app",
    "db",
    "exec_api",
    "make_user",
    "member",
    "phase4a_env",
    "second_member",
    "sessionmaker_",
]

# Every checkpoint table that has ever existed. Old: Prisma's. New: langgraph-checkpoint-postgres.
CHECKPOINT_TABLES = (
    "langgraph_checkpoints",
    "langgraph_checkpoint_writes",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)
ENQUEUE_SITES = (
    "src.api.executions.enqueue_execution",
    "src.api.approval_email.enqueue_execution",
    "src.api.run.enqueue_execution",
    "src.api.internal.enqueue_execution",
)

_TRACKED_THREADS: list[str] = []


@pytest.fixture(autouse=True)
def _network_lock(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnusedFunction]
    """Any outbound request without a registered mock fails (loopback is excluded by HTTPX_MARK)."""
    del httpx_mock


@pytest_asyncio.fixture(autouse=True)
async def _clean_buckets_after() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    yield
    await reset_rate_limit_buckets()


@pytest_asyncio.fixture(autouse=True)
async def _fresh_notify_connection() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Today the NOTIFY connection is a module global bound to one event loop (each test has its
    own loop). Dropped when the attribute exists; harmless after the phase replaces it."""
    import src.engine.events_notify as events_notify

    def _drop() -> Any:
        conn: Any = getattr(events_notify, "_notify_connection", None)
        if hasattr(events_notify, "_notify_connection"):
            events_notify._notify_connection = None  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        return conn

    _drop()
    yield
    conn = _drop()
    if conn is not None:
        with contextlib.suppress(Exception):
            await conn.close()


@pytest_asyncio.fixture(autouse=True)
async def _purge_tracked_checkpoints(  # pyright: ignore[reportUnusedFunction]
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Teardown: remove every row of every tracked thread from every checkpoint table that
    exists, whichever saver wrote it (4a's own cleanup knows only the old tables)."""
    _TRACKED_THREADS.clear()
    yield
    threads = list(dict.fromkeys(_TRACKED_THREADS))
    _TRACKED_THREADS.clear()
    await purge_threads(sessionmaker_, threads)


async def purge_threads(sm: async_sessionmaker[AsyncSession], threads: list[str]) -> None:
    if not threads:
        return
    async with sm() as s:
        for table in CHECKPOINT_TABLES:
            if await s.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}):
                await s.execute(
                    text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"),
                    {"ids": threads},
                )
        await s.commit()


def track_thread(thread_id: str) -> None:
    _TRACKED_THREADS.append(thread_id)


@dataclass
class RunningApp:
    app: FastAPI
    client: AsyncClient
    sm: async_sessionmaker[AsyncSession]


@contextlib.asynccontextmanager
async def running_app(
    monkeypatch: pytest.MonkeyPatch, *, dev_mode_enqueue: bool = False
) -> AsyncGenerator[RunningApp]:
    """A fresh app process: own lifespan, own checkpointer. Enqueues run claim-and-run on IT,
    unless ``dev_mode_enqueue``: then the real ``enqueue_execution`` is installed at every call
    site (no Cloud Tasks service account: the in-process fallback task runs the claim)."""
    get_settings.cache_clear()
    fresh = create_app()
    async with (
        fresh.router.lifespan_context(fresh),
        AsyncClient(transport=ASGITransport(app=fresh), base_url="http://p5") as c,
    ):

        async def _enqueue(execution_id: str, *, kind: str, **_ignored: Any) -> None:
            resp = await c.post(
                "/internal/claim-and-run", json={"executionId": execution_id, "kind": kind}
            )
            resp.raise_for_status()

        for site in ENQUEUE_SITES:
            if dev_mode_enqueue:
                from src.execution import cloud_tasks

                monkeypatch.setattr(site, cloud_tasks.enqueue_execution)
            else:
                monkeypatch.setattr(site, _enqueue)
        yield RunningApp(fresh, c, fresh.state.db_sessionmaker)


async def checkpoint_row_counts(
    sm: async_sessionmaker[AsyncSession], thread_id: str
) -> dict[str, int]:
    """Rows for ``thread_id`` per checkpoint table that exists in this database."""
    counts: dict[str, int] = {}
    async with sm() as s:
        for table in CHECKPOINT_TABLES:
            if await s.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}):
                counts[table] = int(
                    await s.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE thread_id = :t"),
                        {"t": thread_id},
                    )
                    or 0
                )
    return counts


async def checkpoint_row_count(sm: async_sessionmaker[AsyncSession], thread_id: str) -> int:
    """Rows for ``thread_id`` across every checkpoint table that exists in this database."""
    return sum((await checkpoint_row_counts(sm, thread_id)).values())


async def wait_for_status(
    sm: async_sessionmaker[AsyncSession], execution_id: str, statuses: set[str], timeout: float
) -> str:
    """Poll the row (fresh short session each time) until its status is in ``statuses``."""
    deadline = asyncio.get_running_loop().time() + timeout
    status = ""
    while asyncio.get_running_loop().time() < deadline:
        async with sm() as s:
            status = str(
                await s.scalar(
                    text("SELECT status FROM workflow_executions WHERE id = :i"),
                    {"i": execution_id},
                )
            )
        if status in statuses:
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"{execution_id} stayed {status!r}, wanted {sorted(statuses)}")


def two_approval_graph() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """start -> ua1 -> (approved) set first -> ua2 -> (approved) set second -> end."""
    nodes = [
        start_node(),
        node("ua1", "user-approval", approvalMessage="First", approverEmail="a@example.com"),
        node("one", "set-state", stateKey="first", stateValue="yes"),
        node("ua2", "user-approval", approvalMessage="Second", approverEmail="a@example.com"),
        node("two", "set-state", stateKey="second", stateValue="yes"),
        node("no", "set-state", stateKey="result", stateValue="rejected"),
        end_node(),
    ]
    edges = [
        edge("s", "ua1"),
        edge("ua1", "one", branch="approved"),
        edge("ua1", "no", "ua1-rej", branch="rejected"),
        edge("one", "ua2"),
        edge("ua2", "two", branch="approved"),
        edge("ua2", "no", "ua2-rej", branch="rejected"),
        edge("two", "e"),
        edge("no", "e"),
    ]
    return nodes, edges


def fan_out_graph() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """start fans out to an approval node AND a sibling set-state node (one super-step). The
    sibling completes while the approval interrupts, so its writes are saved as PENDING WRITES of
    the interrupted checkpoint and must not be re-run (or lost) on resume."""
    nodes = [
        start_node(),
        node("ua", "user-approval", approvalMessage="Fan", approverEmail="a@example.com"),
        node("side", "set-state", stateKey="side", stateValue="done"),
        node("ok", "set-state", stateKey="result", stateValue="approved"),
        node("no", "set-state", stateKey="result", stateValue="rejected"),
        node("fin", "set-state", stateKey="fin", stateValue="yes"),
        end_node(),
    ]
    edges = [
        edge("s", "ua"),
        edge("s", "side"),
        edge("ua", "ok", branch="approved"),
        edge("ua", "no", branch="rejected"),
        edge("ok", "fin"),
        edge("no", "fin"),
        edge("side", "fin"),
        edge("fin", "e"),
    ]
    return nodes, edges


async def start_paused(
    client: AsyncClient,
    db: Db,
    who: Member,
    monkeypatch: pytest.MonkeyPatch,
    *,
    graph: Any = approval_graph,
) -> tuple[str, str]:
    """Create a run through ``client``'s app (claim-and-run inline) until it pauses at its
    approval node. Returns (execution_id, thread_id); both are tracked for cleanup."""
    install_email_recorder(monkeypatch)
    nodes, edges = graph()
    wid = await db.seed_workflow(who, nodes=nodes, edges=edges)
    r = await client.post(
        "/executions", json={"workflowId": wid, "input": "x"}, headers=who.headers
    )
    assert r.status_code == 202, r.text
    eid = str(jbody(r)["id"])
    row = await db.must(eid)
    db.tracked_executions.append(eid)
    db.tracked_threads.append(row.thread_id)
    track_thread(row.thread_id)
    assert row.status == "waiting_approval", (row.status, row.error)
    return eid, row.thread_id
