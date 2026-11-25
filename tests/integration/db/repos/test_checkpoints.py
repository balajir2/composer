import re
import secrets
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import LangGraphCheckpoint, LangGraphCheckpointWrite
from src.db.repos import checkpoints as repo

pytestmark = pytest.mark.integration


async def _seed(session: AsyncSession, thread_id: str) -> None:
    session.add(
        LangGraphCheckpoint(
            thread_id=thread_id,
            checkpoint_ns="",
            checkpoint_id=secrets.token_hex(4),
            checkpoint=b"c",
            metadata_=b"m",
        )
    )
    session.add(
        LangGraphCheckpointWrite(
            thread_id=thread_id,
            checkpoint_ns="",
            checkpoint_id=secrets.token_hex(4),
            task_id="t",
            idx=0,
            channel="ch",
            value=b"v",
        )
    )
    await session.flush()


async def _counts(session: AsyncSession, thread_id: str) -> tuple[int, int]:
    a = await session.scalar(
        select(func.count())
        .select_from(LangGraphCheckpoint)
        .where(LangGraphCheckpoint.thread_id == thread_id)
    )
    b = await session.scalar(
        select(func.count())
        .select_from(LangGraphCheckpointWrite)
        .where(LangGraphCheckpointWrite.thread_id == thread_id)
    )
    return a or 0, b or 0


async def test_delete_threads_removes_both_tables_for_exactly_those_threads(
    session: AsyncSession,
) -> None:
    t1, t2, keep = (f"t-{secrets.token_hex(4)}" for _ in range(3))
    for t in (t1, t2, keep):
        await _seed(session, t)
    await repo.delete_threads(session, [t1, t2])
    assert await _counts(session, t1) == (0, 0)
    assert await _counts(session, t2) == (0, 0)
    assert await _counts(session, keep) == (1, 1)


async def test_delete_threads_with_no_threads_is_a_noop(session: AsyncSession) -> None:
    keep = f"t-{secrets.token_hex(4)}"
    await _seed(session, keep)
    await repo.delete_threads(session, [])
    assert await _counts(session, keep) == (1, 1)


async def test_delete_threads_accepts_more_thread_ids_than_postgres_has_parameters(
    session: AsyncSession,
) -> None:
    keep, gone = (f"t-{secrets.token_hex(4)}" for _ in range(2))
    await _seed(session, keep)
    await _seed(session, gone)
    await repo.delete_threads(session, [f"absent-{i}" for i in range(70_000)] + [gone])
    assert await _counts(session, gone) == (0, 0)
    assert await _counts(session, keep) == (1, 1)


# --- legacy_* functions (LegacyCheckpointSaver's SQL) ---


def _row(
    thread: str, cid: str, parent: str | None = None, ns: str = ""
) -> repo.LegacyCheckpointRow:
    return repo.LegacyCheckpointRow(
        thread_id=thread,
        checkpoint_ns=ns,
        checkpoint_id=cid,
        parent_checkpoint_id=parent,
        checkpoint=b"c-" + cid.encode(),
        metadata=b"m-" + cid.encode(),
    )


async def test_legacy_upsert_get_and_replace(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    await repo.legacy_upsert_checkpoint(session, _row(t, "a"))
    got = await repo.legacy_get(session, t, "", "a")
    assert got == _row(t, "a")
    await repo.legacy_upsert_checkpoint(
        session,
        repo.LegacyCheckpointRow(t, "", "a", "p", b"new", b"newm"),
    )
    got = await repo.legacy_get(session, t, "", "a")
    assert got == repo.LegacyCheckpointRow(t, "", "a", "p", b"new", b"newm")
    assert await repo.legacy_get(session, t, "", "zzz") is None
    assert await repo.legacy_get(session, t, "other-ns", "a") is None


async def test_legacy_get_without_id_is_newest_by_checkpoint_id(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    await repo.legacy_upsert_checkpoint(session, _row(t, "b"))
    await repo.legacy_upsert_checkpoint(session, _row(t, "a"))
    await session.execute(
        text("UPDATE langgraph_checkpoints SET created_at = now() WHERE thread_id = :t"),
        {"t": t},
    )
    latest = await repo.legacy_get(session, t, "", None)
    assert latest is not None and latest.checkpoint_id == "b"


def _ids(rows: list[repo.LegacyCheckpointRow]) -> list[str]:
    return [r.checkpoint_id for r in rows]


async def test_legacy_list_order_before_limit_and_namespaces(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    for cid in ("a", "b", "c"):
        await repo.legacy_upsert_checkpoint(session, _row(t, cid))
    await repo.legacy_upsert_checkpoint(session, _row(t, "z", ns="sub"))
    assert _ids(await repo.legacy_list(session, t, "", before_id=None, limit=None)) == [
        "c",
        "b",
        "a",
    ]
    assert _ids(await repo.legacy_list(session, t, "", before_id="c", limit=None)) == ["b", "a"]
    assert _ids(await repo.legacy_list(session, t, "", before_id=None, limit=1)) == ["c"]
    assert await repo.legacy_namespaces(session, t) == ["", "sub"]
    assert await repo.legacy_namespaces(session, "absent") == []


async def test_legacy_writes_upsert_and_order(session: AsyncSession) -> None:
    t = f"t-{secrets.token_hex(4)}"
    await repo.legacy_upsert_writes(session, t, "", "c1", "tb", [(1, "x", b"1"), (0, "y", b"0")])
    await repo.legacy_upsert_writes(session, t, "", "c1", "ta", [(0, "z", b"2")])
    await repo.legacy_upsert_writes(session, t, "", "c1", "tb", [(0, "y2", b"9")])
    got = await repo.legacy_writes(session, t, "", "c1")
    assert got == [
        repo.LegacyWriteRow("ta", 0, "z", b"2"),
        repo.LegacyWriteRow("tb", 0, "y2", b"9"),
        repo.LegacyWriteRow("tb", 1, "x", b"1"),
    ]
    assert await repo.legacy_writes(session, t, "", "other") == []
    await repo.legacy_upsert_writes(session, t, "", "c1", "tc", [])  # no-op


# --- delete_threads across the official saver's tables (decision D7) ---


async def _seed_saver_rows(session: AsyncSession, thread_id: str) -> None:
    cid = secrets.token_hex(4)
    await session.execute(
        text(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata)"
            " VALUES (:t, '', :c, '{}'::jsonb, '{}'::jsonb)"
        ),
        {"t": thread_id, "c": cid},
    )
    await session.execute(
        text(
            "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)"
            " VALUES (:t, '', 'ch', '1', 'msgpack', decode('00', 'hex'))"
        ),
        {"t": thread_id},
    )
    await session.execute(
        text(
            "INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,"
            " channel, type, blob) VALUES (:t, '', :c, 'task', 0, 'ch', 'msgpack', decode('00', 'hex'))"
        ),
        {"t": thread_id, "c": cid},
    )
    await session.flush()


async def _saver_counts(session: AsyncSession, thread_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in repo.SAVER_THREAD_TABLES:
        out[table] = int(
            await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE thread_id = :t"), {"t": thread_id}
            )
            or 0
        )
    return out


async def test_delete_threads_clears_all_five_tables_for_those_threads_only(
    session: AsyncSession,
) -> None:
    gone, keep = (f"t-{secrets.token_hex(4)}" for _ in range(2))
    for t in (gone, keep):
        await _seed(session, t)
        await _seed_saver_rows(session, t)
    await repo.delete_threads(session, [gone])
    assert await _counts(session, gone) == (0, 0)
    assert set((await _saver_counts(session, gone)).values()) == {0}
    assert await _counts(session, keep) == (1, 1)
    assert set((await _saver_counts(session, keep)).values()) == {1}


async def test_delete_threads_runs_in_the_callers_transaction(engine: AsyncEngine) -> None:
    t = f"t-{secrets.token_hex(4)}"
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            await _seed(s, t)
            await _seed_saver_rows(s, t)
            await s.commit()
        async with sm() as s:
            await repo.delete_threads(s, [t])
            await s.rollback()
        async with sm() as s:
            assert await _counts(s, t) == (1, 1)
            assert set((await _saver_counts(s, t)).values()) == {1}
    finally:
        async with sm() as s:
            await repo.delete_threads(s, [t])
            await s.commit()


async def test_delete_threads_matches_adelete_thread(
    engine: AsyncEngine, test_database_url: str
) -> None:
    """Deleting through the repo leaves the official saver with nothing to read, exactly as the
    saver's own ``adelete_thread`` does."""
    from src.checkpoints.saver import build_saver_pool
    from src.config import get_settings
    from src.engine.graph_builder import build_graph
    from src.engine.state import initial_state
    from src.engine.workflow import Workflow
    from tests.integration.sqla_phase4a.conftest import approval_graph

    pool = build_saver_pool(get_settings())
    await pool.open()
    sm = async_sessionmaker(engine, expire_on_commit=False)
    via_repo, via_saver = (f"t-{secrets.token_hex(4)}" for _ in range(2))
    try:
        saver = AsyncPostgresSaver(pool)
        nodes, edges = approval_graph()
        wf = Workflow.model_validate({"id": "w", "name": "n", "nodes": nodes, "edges": edges})
        graph = build_graph(wf, saver)
        for t in (via_repo, via_saver):
            await graph.ainvoke(initial_state("x"), config={"configurable": {"thread_id": t}})
        cfg: Any = {"configurable": {"thread_id": via_repo, "checkpoint_ns": ""}}
        assert await saver.aget_tuple(cfg) is not None
        async with sm() as s:
            await repo.delete_threads(s, [via_repo])
            await s.commit()
        await saver.adelete_thread(via_saver)
        for t in (via_repo, via_saver):
            cfg = {"configurable": {"thread_id": t, "checkpoint_ns": ""}}
            assert await saver.aget_tuple(cfg) is None
            assert [x async for x in saver.alist(cfg)] == []
        async with sm() as s:
            assert await _saver_counts(s, via_repo) == await _saver_counts(s, via_saver)
            assert set((await _saver_counts(s, via_repo)).values()) == {0}
    finally:
        async with sm() as s:
            await repo.delete_threads(s, [via_repo, via_saver])
            await s.commit()
        await pool.close()


def test_delete_threads_covers_every_saver_table() -> None:
    """If the library ever adds a table keyed by thread_id, this fails until delete_threads
    clears it too."""
    from langgraph.checkpoint.postgres import BasePostgresSaver

    migrations = "\n".join(BasePostgresSaver.MIGRATIONS)
    tables = {
        name
        for name, body in re.findall(
            r"CREATE TABLE IF NOT EXISTS (\w+) \(([^;]*?)\);", migrations, flags=re.DOTALL
        )
        if "thread_id" in body
    }
    assert tables == set(repo.SAVER_THREAD_TABLES)


# --- legacy_ahead_namespaces (copy-on-resume's comparison, same SQL as the deploy guard) ---


async def _put_saver_checkpoint(session: AsyncSession, thread: str, cid: str, ns: str = "") -> None:
    await session.execute(
        text(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata)"
            " VALUES (:t, :n, :c, '{}'::jsonb, '{}'::jsonb)"
        ),
        {"t": thread, "n": ns, "c": cid},
    )


async def test_legacy_ahead_namespaces_lists_only_namespaces_the_old_tables_lead(
    session: AsyncSession,
) -> None:
    t = f"t-{secrets.token_hex(4)}"
    for ns, old_id, new_id in (
        ("", "1ef-b", "1ef-a"),  # old newer -> ahead
        ("same", "1ef-c", "1ef-c"),  # level -> not ahead
        ("new-ahead", "1ef-a", "1ef-d"),  # new newer -> not ahead
        ("old-only", "1ef-a", None),  # absent in new -> ahead
    ):
        await repo.legacy_upsert_checkpoint(session, _row(t, old_id, ns=ns))
        if new_id is not None:
            await _put_saver_checkpoint(session, t, new_id, ns)
    assert await repo.legacy_ahead_namespaces(session, t) == ["", "old-only"]
    assert await repo.legacy_ahead(session, t) is True
    assert await repo.legacy_ahead_namespaces(session, "absent") == []
