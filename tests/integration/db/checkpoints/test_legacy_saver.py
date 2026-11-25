"""LegacyCheckpointSaver against real Postgres (old langgraph_checkpoints tables)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, empty_checkpoint
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.checkpoints.legacy import LegacyCheckpointSaver
from src.db.repos import checkpoints as repo

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[3] / "fixtures" / "legacy_checkpoint_rows.json"


def cp(i: int, **values: Any) -> Checkpoint:
    c = empty_checkpoint()
    c["id"] = f"{i:04d}-checkpoint"
    c["ts"] = f"2026-01-01T00:00:0{i % 10}+00:00"
    c["channel_values"] = values
    c["channel_versions"] = dict.fromkeys(values, i)
    return c


def cfg(thread: str, checkpoint_id: str | None = None, ns: str = "") -> RunnableConfig:
    c: dict[str, Any] = {"thread_id": thread, "checkpoint_ns": ns}
    if checkpoint_id:
        c["checkpoint_id"] = checkpoint_id
    return {"configurable": c}


async def chain3(saver: LegacyCheckpointSaver, thread: str) -> list[RunnableConfig]:
    out: list[RunnableConfig] = []
    parent = cfg(thread)
    for i in (1, 2, 3):
        parent = await saver.aput(parent, cp(i, a=f"v{i}"), {"step": i, "source": "loop"}, {"a": i})
        out.append(parent)
    return out


async def test_round_trip_latest_by_id_and_history(
    legacy: LegacyCheckpointSaver, new_thread: Callable[[], str]
) -> None:
    t = new_thread()
    cfgs = await chain3(legacy, t)
    await legacy.aput_writes(cfgs[2], [("a", "w1"), ("b", {"x": 1})], "task-b")
    await legacy.aput_writes(cfgs[2], [("c", [1, 2])], "task-a")

    latest = await legacy.aget_tuple(cfg(t))
    assert latest is not None
    assert latest.checkpoint["id"] == "0003-checkpoint"
    assert latest.checkpoint["channel_values"] == {"a": "v3"}
    assert latest.checkpoint["channel_versions"] == {"a": 3}
    assert latest.metadata == {"step": 3, "source": "loop"}
    assert latest.parent_config == cfg(t, "0002-checkpoint")
    assert latest.pending_writes == [
        ("task-a", "c", [1, 2]),
        ("task-b", "a", "w1"),
        ("task-b", "b", {"x": 1}),
    ]

    by_id = await legacy.aget_tuple(cfg(t, "0001-checkpoint"))
    assert by_id is not None and by_id.parent_config is None and by_id.pending_writes == []
    assert await legacy.aget_tuple(cfg(t, "nope")) is None
    assert await legacy.aget_tuple(cfg("absent-thread")) is None

    ids = [x.checkpoint["id"] async for x in legacy.alist(cfg(t))]
    assert ids == ["0003-checkpoint", "0002-checkpoint", "0001-checkpoint"]
    ids = [x.checkpoint["id"] async for x in legacy.alist(cfg(t), before=cfgs[2])]
    assert ids == ["0002-checkpoint", "0001-checkpoint"]
    ids = [x.checkpoint["id"] async for x in legacy.alist(cfg(t), limit=1)]
    assert ids == ["0003-checkpoint"]


async def test_namespaces_are_separate(
    legacy: LegacyCheckpointSaver, new_thread: Callable[[], str]
) -> None:
    t = new_thread()
    await legacy.aput(cfg(t), cp(1, a="root"), {}, {})
    await legacy.aput(cfg(t, ns="sub"), cp(2, a="sub"), {}, {})
    root = await legacy.aget_tuple(cfg(t))
    sub = await legacy.aget_tuple(cfg(t, ns="sub"))
    assert root is not None and root.checkpoint["channel_values"] == {"a": "root"}
    assert sub is not None and sub.checkpoint["channel_values"] == {"a": "sub"}


async def test_aput_and_writes_are_upserts(
    legacy: LegacyCheckpointSaver, new_thread: Callable[[], str]
) -> None:
    t = new_thread()
    c = await legacy.aput(cfg(t), cp(1, a="old"), {"step": 1}, {})
    await legacy.aput(cfg(t), cp(1, a="new"), {"step": 2}, {})
    await legacy.aput_writes(c, [("ch", "old")], "task")
    await legacy.aput_writes(c, [("ch", "new")], "task")
    got = await legacy.aget_tuple(cfg(t))
    assert got is not None
    assert got.checkpoint["channel_values"] == {"a": "new"} and got.metadata == {"step": 2}
    assert got.pending_writes == [("task", "ch", "new")]


async def test_adelete_thread_removes_only_that_thread(
    legacy: LegacyCheckpointSaver, new_thread: Callable[[], str]
) -> None:
    a, b = new_thread(), new_thread()
    for t in (a, b):
        cfgs = await chain3(legacy, t)
        await legacy.aput_writes(cfgs[2], [("x", 1)], "task")
    await legacy.adelete_thread(a)
    assert await legacy.aget_tuple(cfg(a)) is None
    kept = await legacy.aget_tuple(cfg(b))
    assert kept is not None and kept.pending_writes == [("task", "x", 1)]


async def test_same_millisecond_checkpoints_order_by_id(
    legacy: LegacyCheckpointSaver,
    sessionmaker_: async_sessionmaker[AsyncSession],
    new_thread: Callable[[], str],
) -> None:
    """D9: created_at has millisecond precision; ties must resolve by checkpoint_id."""
    t = new_thread()
    await legacy.aput(cfg(t), cp(2, a="second"), {}, {})
    await legacy.aput(cfg(t), cp(1, a="first"), {}, {})
    async with sessionmaker_() as s:
        await s.execute(
            text(
                "UPDATE langgraph_checkpoints SET created_at = '2026-01-01T00:00:00Z' "
                "WHERE thread_id = :t"
            ),
            {"t": t},
        )
        await s.commit()
    latest = await legacy.aget_tuple(cfg(t))
    assert latest is not None and latest.checkpoint["id"] == "0002-checkpoint"
    ids = [x.checkpoint["id"] async for x in legacy.alist(cfg(t))]
    assert ids == ["0002-checkpoint", "0001-checkpoint"]


async def test_sync_methods_raise(legacy: LegacyCheckpointSaver) -> None:
    with pytest.raises(NotImplementedError, match="Use the async API"):
        legacy.get_tuple(cfg("t"))
    with pytest.raises(NotImplementedError, match="Use the async API"):
        legacy.put(cfg("t"), cp(1), {}, {})
    with pytest.raises(NotImplementedError, match="Use the async API"):
        legacy.put_writes(cfg("t"), [], "task")
    with pytest.raises(NotImplementedError, match="Use the async API"):
        list(legacy.list(cfg("t")))


async def test_alist_requires_thread(legacy: LegacyCheckpointSaver) -> None:
    with pytest.raises(ValueError, match="thread_id"):
        async for _ in legacy.alist(None):
            pass


async def test_reads_rows_written_by_prisma(
    legacy: LegacyCheckpointSaver,
    sessionmaker_: async_sessionmaker[AsyncSession],
    new_thread: Callable[[], str],
) -> None:
    """Golden rows dumped from PrismaCheckpointSaver for a paused approval graph."""
    data = json.loads(FIXTURE.read_text())
    t = new_thread()
    async with sessionmaker_() as s:
        for row in data["checkpoints"]:
            await repo.legacy_upsert_checkpoint(
                s,
                repo.LegacyCheckpointRow(
                    thread_id=t,
                    checkpoint_ns=row["checkpoint_ns"],
                    checkpoint_id=row["checkpoint_id"],
                    parent_checkpoint_id=row["parent_checkpoint_id"],
                    checkpoint=bytes.fromhex(row["checkpoint"]),
                    metadata=bytes.fromhex(row["metadata"]),
                ),
            )
        for w in data["writes"]:
            await repo.legacy_upsert_writes(
                s,
                t,
                w["checkpoint_ns"],
                w["checkpoint_id"],
                w["task_id"],
                [(w["idx"], w["channel"], bytes.fromhex(w["value"]))],
            )
        await s.commit()
    latest = await legacy.aget_tuple(cfg(t))
    assert latest is not None
    assert any(ch == "__interrupt__" for _, ch, _ in latest.pending_writes or [])
    assert "variables" in latest.checkpoint["channel_values"]
