"""Deploy guard: which active runs have a newest checkpoint only in the old tables."""

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.base.id import uuid6
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.checkpoints.copy import copy_thread
from src.checkpoints.legacy import LegacyCheckpointSaver
from src.checkpoints.sync import find_unsynced
from src.db.repos import checkpoints as checkpoints_repo
from src.engine.state import initial_state
from tests.integration.db.checkpoints.test_copy_round_trip import (
    compile_graph,
    lg,
    postgres,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from tests.integration.sqla_phase4a.conftest import approval_graph

pytestmark = pytest.mark.integration

MakeRun = Callable[[str], Awaitable[tuple[str, str]]]


@pytest_asyncio.fixture
async def make_run(
    sessionmaker_: async_sessionmaker[AsyncSession], new_thread: Callable[[], str]
) -> AsyncIterator[MakeRun]:
    """Insert an execution with the given status; returns (execution_id, thread_id)."""
    created: list[str] = []
    wf_id = f"guard-wf-{secrets.token_hex(4)}"

    async def make(status: str) -> tuple[str, str]:
        thread = new_thread()
        eid = f"guard-{secrets.token_hex(6)}"
        async with sessionmaker_() as s:
            if not created:
                await s.execute(
                    text(
                        "INSERT INTO workflows (id, name, nodes, edges, updated_at) "
                        "VALUES (:i, 'g', '[]', '[]', now())"
                    ),
                    {"i": wf_id},
                )
            await s.execute(
                text(
                    "INSERT INTO workflow_executions "
                    "(id, workflow_id, status, thread_id, node_results, variables) "
                    "VALUES (:i, :w, :s, :t, '{}', '{}')"
                ),
                {"i": eid, "w": wf_id, "s": status, "t": thread},
            )
            await s.commit()
        created.append(eid)
        return eid, thread

    yield make
    async with sessionmaker_() as s:
        if created:
            await s.execute(text("DELETE FROM workflows WHERE id = :i"), {"i": wf_id})
        await s.commit()


async def listed(sm: async_sessionmaker[AsyncSession], mine: list[str]) -> dict[str, str]:
    return {r.execution_id: r.status for r in await find_unsynced(sm) if r.execution_id in mine}


async def legacy_ahead(sm: async_sessionmaker[AsyncSession], thread: str) -> bool:
    async with sm() as s:
        return await checkpoints_repo.legacy_ahead(s, thread)


async def test_nothing_listed_for_unknown_runs(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    assert await listed(sessionmaker_, []) == {}


async def test_waiting_run_on_old_tables_blocks_then_copy_clears_then_progress_blocks(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,  # noqa: F811
    sessionmaker_: async_sessionmaker[AsyncSession],
    make_run: MakeRun,
) -> None:
    eid, thread = await make_run("waiting_approval")
    await compile_graph(approval_graph, legacy).ainvoke(initial_state("x"), config=lg(thread))
    assert await listed(sessionmaker_, [eid]) == {eid: "waiting_approval"}
    assert await legacy_ahead(sessionmaker_, thread)

    await copy_thread(legacy, postgres, thread, to="postgres", namespaces=[""])
    assert await listed(sessionmaker_, [eid]) == {}
    assert not await legacy_ahead(sessionmaker_, thread)

    # The run progressed on an old instance after the copy: the old tables are ahead again.
    cp: Any = empty_checkpoint()
    cp["id"] = str(uuid6(clock_seq=-2))
    await legacy.aput(
        {"configurable": {"thread_id": thread, "checkpoint_ns": ""}},
        cp,
        {"source": "loop", "step": 99, "parents": {}},
        {},
    )
    assert await listed(sessionmaker_, [eid]) == {eid: "waiting_approval"}


async def test_subgraph_namespace_ahead_blocks(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,  # noqa: F811
    sessionmaker_: async_sessionmaker[AsyncSession],
    make_run: MakeRun,
) -> None:
    """A namespace other than the root is compared too."""
    eid, thread = await make_run("running")
    await compile_graph(approval_graph, legacy).ainvoke(initial_state("x"), config=lg(thread))
    await copy_thread(legacy, postgres, thread, to="postgres", namespaces=[""])
    assert await listed(sessionmaker_, [eid]) == {}
    cp: Any = empty_checkpoint()
    await legacy.aput(
        {"configurable": {"thread_id": thread, "checkpoint_ns": "sub:1"}},
        cp,
        {"source": "loop", "step": 1, "parents": {}},
        {},
    )
    assert await listed(sessionmaker_, [eid]) == {eid: "running"}


async def test_running_run_listed_and_counts(
    legacy: LegacyCheckpointSaver,
    sessionmaker_: async_sessionmaker[AsyncSession],
    make_run: MakeRun,
) -> None:
    eid, thread = await make_run("running")
    await compile_graph(approval_graph, legacy).ainvoke(initial_state("x"), config=lg(thread))
    assert await listed(sessionmaker_, [eid]) == {eid: "running"}
    async with sessionmaker_() as s:
        counts = await checkpoints_repo.active_status_counts(s)
    assert counts["running"] >= 1
    assert set(counts) == {"queued", "running", "waiting_approval"}


@pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
async def test_terminal_runs_ignored(
    legacy: LegacyCheckpointSaver,
    sessionmaker_: async_sessionmaker[AsyncSession],
    make_run: MakeRun,
    status: str,
) -> None:
    eid, thread = await make_run(status)
    await compile_graph(approval_graph, legacy).ainvoke(initial_state("x"), config=lg(thread))
    assert await listed(sessionmaker_, [eid]) == {}
    assert await legacy_ahead(sessionmaker_, thread)  # legacy_ahead ignores status


async def test_queued_run_without_checkpoints_not_listed(
    sessionmaker_: async_sessionmaker[AsyncSession], make_run: MakeRun
) -> None:
    eid, _ = await make_run("queued")
    assert await listed(sessionmaker_, [eid]) == {}


async def test_run_only_in_new_tables_not_listed(
    postgres: AsyncPostgresSaver,  # noqa: F811
    sessionmaker_: async_sessionmaker[AsyncSession],
    make_run: MakeRun,
) -> None:
    eid, thread = await make_run("waiting_approval")
    await compile_graph(approval_graph, postgres).ainvoke(initial_state("x"), config=lg(thread))
    assert await listed(sessionmaker_, [eid]) == {}
