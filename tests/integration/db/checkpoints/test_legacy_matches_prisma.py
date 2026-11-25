"""LegacyCheckpointSaver must behave exactly like PrismaCheckpointSaver on the old tables.

Deleted in phase 5 Task 7 together with PrismaCheckpointSaver. The only intended difference is
D9: "newest" and "before" order by checkpoint_id, not created_at (identical whenever ids ascend in
write order, which every scenario here guarantees).
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.types import Command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.checkpoints.legacy import LegacyCheckpointSaver
from src.engine.graph_builder import build_graph
from src.engine.state import initial_state
from src.engine.workflow import Workflow
from src.storage.checkpointer import PrismaCheckpointSaver
from tests.integration.db.checkpoints.test_legacy_saver import cfg, cp
from tests.integration.sqla_phase4a.conftest import approval_graph

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[3] / "fixtures" / "legacy_checkpoint_rows.json"


@dataclass
class Savers:
    prisma: PrismaCheckpointSaver
    legacy: LegacyCheckpointSaver
    sm: async_sessionmaker[AsyncSession]


@pytest.fixture
def savers(app: FastAPI, legacy: LegacyCheckpointSaver) -> Savers:
    return Savers(PrismaCheckpointSaver(app.state.db), legacy, app.state.db_sessionmaker)


def norm(t: CheckpointTuple | None, thread: str) -> Any:
    """Tuple as a comparable structure with the thread id masked."""
    if t is None:
        return None
    return json.loads(json.dumps(t._asdict(), default=repr).replace(thread, "THREAD"))


async def scenario(saver: BaseCheckpointSaver[Any], thread: str) -> dict[str, Any]:
    """Every operation both savers must answer identically."""
    parent = cfg(thread)
    cfgs: list[RunnableConfig] = []
    for i in (1, 2, 3, 4):
        parent = await saver.aput(
            parent,
            cp(i, a=f"v{i}", nested={"k": [i, None, 1.5]}),
            {"step": i, "source": "loop", "parents": {"n": str(i)}},
            {"a": i, "nested": i},
        )
        cfgs.append(parent)
        await asyncio.sleep(0.01)  # distinct created_at for the Prisma ordering
    await saver.aput(cfg(thread, ns="sub"), cp(9, a="sub"), {"step": 0}, {})
    await saver.aput_writes(cfgs[3], [("a", "w1"), ("b", {"x": 1}), ("a", None)], "task-b")
    await saver.aput_writes(cfgs[3], [("__interrupt__", ["pause"])], "task-a")
    await saver.aput_writes(cfgs[3], [("a", "w1-replaced")], "task-b")  # upsert of idx 0
    await saver.aput(cfgs[1], cp(2, a="rewritten"), {"step": 2}, {})  # upsert checkpoint

    out: dict[str, Any] = {
        "latest": norm(await saver.aget_tuple(cfg(thread)), thread),
        "sub": norm(await saver.aget_tuple(cfg(thread, ns="sub")), thread),
        "missing_id": await saver.aget_tuple(cfg(thread, "nope")),
        "missing_thread": await saver.aget_tuple(cfg(thread + "-absent")),
    }
    for i in (1, 2, 3, 4):
        out[f"id{i}"] = norm(await saver.aget_tuple(cfg(thread, f"{i:04d}-checkpoint")), thread)
    out["list"] = [norm(x, thread) async for x in saver.alist(cfg(thread))]
    out["limit"] = [norm(x, thread) async for x in saver.alist(cfg(thread), limit=2)]
    out["before"] = [norm(x, thread) async for x in saver.alist(cfg(thread), before=cfgs[2])]
    out["before_limit"] = [
        norm(x, thread) async for x in saver.alist(cfg(thread), before=cfgs[3], limit=1)
    ]
    out["before_unknown"] = [
        norm(x, thread) async for x in saver.alist(cfg(thread), before=cfg(thread, "unknown"))
    ]
    out["sub_list"] = [norm(x, thread) async for x in saver.alist(cfg(thread, ns="sub"))]
    return out


async def raw_rows(sm: async_sessionmaker[AsyncSession], thread: str) -> dict[str, Any]:
    async with sm() as s:
        cps = (
            await s.execute(
                text(
                    "SELECT checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                    "encode(checkpoint,'hex'), encode(metadata,'hex') "
                    "FROM langgraph_checkpoints WHERE thread_id=:t ORDER BY 1, 2"
                ),
                {"t": thread},
            )
        ).all()
        ws = (
            await s.execute(
                text(
                    "SELECT checkpoint_ns, checkpoint_id, task_id, idx, channel, "
                    "encode(value,'hex') FROM langgraph_checkpoint_writes "
                    "WHERE thread_id=:t ORDER BY 1, 2, 3, 4"
                ),
                {"t": thread},
            )
        ).all()
    return {"cps": [tuple(r) for r in cps], "ws": [tuple(r) for r in ws]}


async def test_same_scenario_same_results_and_same_bytes(
    savers: Savers, new_thread: Callable[[], str]
) -> None:
    tp, tl = new_thread(), new_thread()
    got_prisma = await scenario(savers.prisma, tp)
    got_legacy = await scenario(savers.legacy, tl)
    assert got_legacy == got_prisma
    assert got_prisma["latest"]["checkpoint"]["id"] == "0004-checkpoint"
    # channel-version integers survive as integers
    assert got_legacy["id3"]["checkpoint"]["channel_versions"] == {"a": 3, "nested": 3}
    assert await raw_rows(savers.sm, tl) == await raw_rows(savers.sm, tp)


@pytest.mark.parametrize("writer", ["prisma", "legacy"])
async def test_rows_written_by_one_are_read_identically_by_both(
    savers: Savers, new_thread: Callable[[], str], writer: str
) -> None:
    t = new_thread()
    w: BaseCheckpointSaver[Any] = getattr(savers, writer)
    await scenario(w, t)
    assert await scenario_reads(savers.prisma, t) == await scenario_reads(savers.legacy, t)


async def scenario_reads(saver: BaseCheckpointSaver[Any], thread: str) -> Any:
    latest = await saver.aget_tuple(cfg(thread))
    history = [x async for x in saver.alist(cfg(thread))]
    return norm(latest, thread), [norm(x, thread) for x in history]


async def test_delete_thread_is_the_same(savers: Savers, new_thread: Callable[[], str]) -> None:
    results: list[Any] = []
    for saver in (savers.prisma, savers.legacy):
        gone, kept = new_thread(), new_thread()
        for t in (gone, kept):
            await scenario(saver, t)
        await saver.adelete_thread(gone)
        results.append(
            (
                await saver.aget_tuple(cfg(gone)),
                [x async for x in saver.alist(cfg(gone))],
                await raw_rows(savers.sm, gone),
                (await raw_rows(savers.sm, kept))["ws"] != [],
            )
        )
    assert results[0] == results[1] == (None, [], {"cps": [], "ws": []}, True)


async def _compiled(saver: BaseCheckpointSaver[Any]) -> Any:
    nodes, edges = approval_graph()
    wf = Workflow.model_validate({"id": "wf", "name": "n", "nodes": nodes, "edges": edges})
    return build_graph(wf, saver)


@pytest.mark.parametrize(("first", "second"), [("legacy", "prisma"), ("prisma", "legacy")])
async def test_graph_paused_with_one_saver_resumes_with_the_other(
    savers: Savers, new_thread: Callable[[], str], first: str, second: str
) -> None:
    t = new_thread()
    lg: Any = {"configurable": {"thread_id": t}}
    paused = await _compiled(getattr(savers, first))
    await paused.ainvoke(initial_state("x"), config=lg)
    snap = await paused.aget_state(lg)
    assert snap.next

    resumed = await _compiled(getattr(savers, second))
    final = await resumed.ainvoke(Command(resume="approved"), config=lg)
    assert final["variables"]["result"] == "approved"
    assert not (await resumed.aget_state(lg)).next


async def write_golden_fixture(saver: PrismaCheckpointSaver, sm: Any, thread: str) -> None:
    """One-off helper, run manually once (never called by the suite): pause an approval graph
    with PrismaCheckpointSaver, then dump its rows as hex into the golden fixture."""
    lg: Any = {"configurable": {"thread_id": thread}}
    compiled = await _compiled(saver)
    await compiled.ainvoke(initial_state("x"), config=lg)
    rows = await raw_rows(sm, thread)
    data = {
        "checkpoints": [
            {
                "checkpoint_ns": r[0],
                "checkpoint_id": r[1],
                "parent_checkpoint_id": r[2],
                "checkpoint": r[3],
                "metadata": r[4],
            }
            for r in rows["cps"]
        ],
        "writes": [
            {
                "checkpoint_ns": r[0],
                "checkpoint_id": r[1],
                "task_id": r[2],
                "idx": r[3],
                "channel": r[4],
                "value": r[5],
            }
            for r in rows["ws"]
        ],
    }
    FIXTURE.write_text(json.dumps(data, indent=1))
