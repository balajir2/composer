"""The copy tool between the old and the official checkpoint tables, both directions, then RESUME.

Real graphs (approval, the fan-out graph whose interrupted checkpoint carries a completed
sibling's pending writes, and a chained pause) on real Postgres. After each copy the run is
resumed on the other saver: it must complete, and no node that already ran may run again.
"""

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.checkpoints import copy as copy_mod
from src.checkpoints.copy import copy_thread
from src.checkpoints.legacy import LegacyCheckpointSaver
from src.checkpoints.saver import build_saver_pool
from src.checkpoints.versions import Target
from src.config import get_settings
from src.engine.graph_builder import build_graph
from src.engine.state import initial_state
from src.engine.workflow import Workflow
from src.executors.set_state import SetStateExecutor
from tests.integration.sqla_phase4a.conftest import approval_graph
from tests.integration.sqla_phase5.conftest import fan_out_graph, two_approval_graph

pytestmark = pytest.mark.integration

OLD_TABLES = ("langgraph_checkpoints", "langgraph_checkpoint_writes")
NEW_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
GraphFn = Callable[[], tuple[list[dict[str, Any]], list[dict[str, Any]]]]


@pytest_asyncio.fixture
async def postgres(
    sessionmaker_: async_sessionmaker[AsyncSession], threads: list[str]
) -> AsyncIterator[AsyncPostgresSaver]:
    pool = build_saver_pool(get_settings())
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool)
        async with sessionmaker_() as s:
            for table in NEW_TABLES:
                await s.execute(
                    text(f"DELETE FROM {table} WHERE thread_id = ANY(:t)"), {"t": threads}
                )
            await s.commit()
    finally:
        await pool.close()


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Ids of set-state nodes in the order they execute."""
    log: list[str] = []
    original = SetStateExecutor.arun

    async def counting(self: SetStateExecutor, state: Any) -> Any:
        log.append(self.node.id)
        return await original(self, state)

    monkeypatch.setattr(SetStateExecutor, "arun", counting)
    return log


def compile_graph(graph_fn: GraphFn, saver: BaseCheckpointSaver[Any]) -> Any:
    nodes, edges = graph_fn()
    wf = Workflow.model_validate({"id": "wf", "name": "n", "nodes": nodes, "edges": edges})
    return build_graph(wf, saver)


def lg(thread: str) -> Any:
    return {"configurable": {"thread_id": thread}}


async def history(saver: BaseCheckpointSaver[Any], thread: str) -> list[CheckpointTuple]:
    cfg: Any = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    return [t async for t in saver.alist(cfg)]


def shape(h: list[CheckpointTuple]) -> list[Any]:
    """Saver-independent view: ids, parent chain, pending (task, channel) multiset."""
    return [
        (
            (t.config.get("configurable") or {}).get("checkpoint_id"),
            (t.parent_config or {}).get("configurable", {}).get("checkpoint_id"),
            sorted((tid, ch) for tid, ch, _ in t.pending_writes or []),
        )
        for t in h
    ]


async def old_rows(sm: async_sessionmaker[AsyncSession], thread: str) -> list[Any]:
    async with sm() as s:
        out: list[Any] = []
        for table in OLD_TABLES:
            res = await s.execute(
                text(f"SELECT * FROM {table} WHERE thread_id = :t ORDER BY 1,2,3,4,5"),
                {"t": thread},
            )
            out.append([tuple(r) for r in res])
        return out


@pytest.mark.parametrize("graph_fn", [approval_graph, fan_out_graph])
async def test_legacy_to_postgres_then_resume(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    sessionmaker_: async_sessionmaker[AsyncSession],
    new_thread: Callable[[], str],
    ran: list[str],
    graph_fn: GraphFn,
) -> None:
    t = new_thread()
    await compile_graph(graph_fn, legacy).ainvoke(initial_state("x"), config=lg(t))
    ran_before = list(ran)
    before = await old_rows(sessionmaker_, t)
    report = await copy_thread(legacy, postgres, t, to="postgres", namespaces=[""])
    assert report.checkpoints == len(await history(legacy, t)) > 0
    assert await old_rows(sessionmaker_, t) == before  # the source is never modified

    resumed = compile_graph(graph_fn, postgres)
    final = await resumed.ainvoke(Command(resume="approved"), config=lg(t))
    assert final["variables"]["result"] == "approved"
    assert (await resumed.aget_state(lg(t))).next == ()
    after = ran[len(ran_before) :]
    if graph_fn is fan_out_graph:
        assert final["variables"]["side"] == "done"
        assert "side" not in after  # the sibling's pending write was kept, not re-run
    assert not set(ran_before) & set(after)


@pytest.mark.parametrize("graph_fn", [approval_graph, fan_out_graph])
async def test_postgres_to_legacy_then_resume(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    new_thread: Callable[[], str],
    ran: list[str],
    graph_fn: GraphFn,
) -> None:
    """Rollback: the old revision's saver (byte-compatible with ``LegacyCheckpointSaver``, proven
    against the Prisma saver in phase 5 Task 4) resumes what the new revision paused."""
    t = new_thread()
    await compile_graph(graph_fn, postgres).ainvoke(initial_state("x"), config=lg(t))
    ran_before = list(ran)
    await copy_thread(postgres, legacy, t, to="legacy", namespaces=[""])

    resumed = compile_graph(graph_fn, legacy)
    final = await resumed.ainvoke(Command(resume="approved"), config=lg(t))
    assert final["variables"]["result"] == "approved"
    assert (await resumed.aget_state(lg(t))).next == ()
    if graph_fn is fan_out_graph:
        assert final["variables"]["side"] == "done"
    assert not set(ran_before) & set(ran[len(ran_before) :])


async def test_chained_pause_round_trip(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    new_thread: Callable[[], str],
    ran: list[str],
) -> None:
    t = new_thread()
    await compile_graph(two_approval_graph, legacy).ainvoke(initial_state("x"), config=lg(t))
    assert ran == []
    await copy_thread(legacy, postgres, t, to="postgres", namespaces=[""])

    on_pg = compile_graph(two_approval_graph, postgres)
    await on_pg.ainvoke(Command(resume="approved"), config=lg(t))
    assert (await on_pg.aget_state(lg(t))).next  # paused again at ua2
    assert ran == ["one"]
    await copy_thread(postgres, legacy, t, to="legacy", namespaces=[""])

    on_legacy = compile_graph(two_approval_graph, legacy)
    final = await on_legacy.ainvoke(Command(resume="approved"), config=lg(t))
    assert final["variables"]["first"] == "yes" and final["variables"]["second"] == "yes"
    assert (await on_legacy.aget_state(lg(t))).next == ()
    assert ran == ["one", "two"]


async def test_copy_is_idempotent(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    sessionmaker_: async_sessionmaker[AsyncSession],
    new_thread: Callable[[], str],
) -> None:
    t = new_thread()
    await compile_graph(fan_out_graph, legacy).ainvoke(initial_state("x"), config=lg(t))

    async def counts() -> list[int]:
        async with sessionmaker_() as s:
            return [
                int(
                    await s.scalar(
                        text(f"SELECT count(*) FROM {tb} WHERE thread_id = :t"), {"t": t}
                    )
                    or 0
                )
                for tb in NEW_TABLES
            ]

    def view(h: list[CheckpointTuple]) -> list[Any]:
        return [(x.checkpoint, x.metadata, x.pending_writes) for x in h]

    await copy_thread(legacy, postgres, t, to="postgres", namespaces=[""])
    first = view(await history(postgres, t))
    first_counts = await counts()
    await copy_thread(legacy, postgres, t, to="postgres", namespaces=[""])
    assert view(await history(postgres, t)) == first
    assert await counts() == first_counts

    # and back: twice gives the same old-table rows
    await copy_thread(postgres, legacy, t, to="legacy", namespaces=[""])
    rows = await old_rows(sessionmaker_, t)
    await copy_thread(postgres, legacy, t, to="legacy", namespaces=[""])
    assert await old_rows(sessionmaker_, t) == rows


async def test_naive_copy_without_conversion_breaks_resume(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    new_thread: Callable[[], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why conversion exists: string versions on the integer saver cannot get a next version."""
    t = new_thread()
    await compile_graph(approval_graph, postgres).ainvoke(initial_state("x"), config=lg(t))

    def identity(cp: Any, _to: Any) -> Any:
        return cp

    monkeypatch.setattr(copy_mod, "convert_checkpoint", identity)
    await copy_thread(postgres, legacy, t, to="legacy", namespaces=[""])
    with pytest.raises(NotImplementedError):
        await compile_graph(approval_graph, legacy).ainvoke(
            Command(resume="approved"), config=lg(t)
        )


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("graph_fn", [approval_graph, fan_out_graph, two_approval_graph])
@pytest.mark.parametrize("direction", ["postgres", "legacy"])
async def test_history_shape_survives_copy(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    new_thread: Callable[[], str],
    graph_fn: GraphFn,
    direction: Target,
    completed: bool,
) -> None:
    """Includes COMPLETED runs: their tasks carry special and ordinary writes together."""
    t = new_thread()
    src, dst = (legacy, postgres) if direction == "postgres" else (postgres, legacy)
    graph = compile_graph(graph_fn, src)
    await graph.ainvoke(initial_state("x"), config=lg(t))
    if completed:
        while (await graph.aget_state(lg(t))).next:
            await graph.ainvoke(Command(resume="approved"), config=lg(t))
    await copy_thread(src, dst, t, to=direction, namespaces=[""])
    got, want = shape(await history(dst, t)), shape(await history(src, t))
    assert got == want
    assert len(want) > 1


@pytest.mark.parametrize("failing", ["aput_writes", "aput"])
async def test_crash_mid_copy_leaves_target_visibly_behind(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    new_thread: Callable[[], str],
    ran: list[str],
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    """A copy that dies on the newest checkpoint must not look caught up (newest ids are what the
    guard and copy-on-resume compare), and a re-copy must then finish the job."""
    t = new_thread()
    await compile_graph(fan_out_graph, legacy).ainvoke(initial_state("x"), config=lg(t))
    ran_before = list(ran)
    src_newest = (await history(legacy, t))[0].config.get("configurable", {})["checkpoint_id"]

    original = getattr(postgres, failing)

    async def boom(config: Any, *args: Any, **kwargs: Any) -> Any:
        cid = (
            config["configurable"].get("checkpoint_id")
            if failing == "aput_writes"
            else args[0]["id"]
        )
        if cid == src_newest:
            raise RuntimeError("simulated crash")
        return await original(config, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(postgres, failing, boom)
        with pytest.raises(RuntimeError):
            await copy_thread(legacy, postgres, t, to="postgres", namespaces=[""])
    target = await history(postgres, t)
    assert not target or target[0].config.get("configurable", {})["checkpoint_id"] != src_newest

    await copy_thread(legacy, postgres, t, to="postgres", namespaces=[""])
    resumed = compile_graph(fan_out_graph, postgres)
    final = await resumed.ainvoke(Command(resume="approved"), config=lg(t))
    assert final["variables"]["side"] == "done"
    assert "side" not in ran[len(ran_before) :]


async def test_postgres_to_legacy_leaves_official_tables_untouched(
    legacy: LegacyCheckpointSaver,
    postgres: AsyncPostgresSaver,
    sessionmaker_: async_sessionmaker[AsyncSession],
    new_thread: Callable[[], str],
) -> None:
    t = new_thread()
    await compile_graph(fan_out_graph, postgres).ainvoke(initial_state("x"), config=lg(t))

    async def snapshot() -> list[Any]:
        async with sessionmaker_() as s:
            out: list[Any] = []
            for table in NEW_TABLES:
                res = await s.execute(text(f"SELECT * FROM {table} WHERE thread_id = :t"), {"t": t})
                out.append(sorted(repr(tuple(r)) for r in res))
            return out

    before = await snapshot()
    await copy_thread(postgres, legacy, t, to="legacy", namespaces=[""])
    assert await snapshot() == before


async def test_one_shot_saver_reads_what_the_pool_saver_wrote(
    postgres: AsyncPostgresSaver, new_thread: Callable[[], str]
) -> None:
    from src.checkpoints.saver import open_one_shot_saver

    t = new_thread()
    await compile_graph(approval_graph, postgres).ainvoke(initial_state("x"), config=lg(t))
    async with open_one_shot_saver(get_settings()) as one_shot:
        assert shape(await history(one_shot, t)) == shape(await history(postgres, t))
