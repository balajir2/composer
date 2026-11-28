import re
import secrets
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.repos import checkpoints as repo

pytestmark = pytest.mark.integration


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


async def _seed(session: AsyncSession, thread_id: str) -> None:
    await _seed_saver_rows(session, thread_id)


async def _counts(session: AsyncSession, thread_id: str) -> dict[str, int]:
    return await _saver_counts(session, thread_id)


ONE = {"checkpoint_writes": 1, "checkpoint_blobs": 1, "checkpoints": 1}
NONE = dict.fromkeys(ONE, 0)


async def test_delete_threads_removes_the_saver_tables_for_exactly_those_threads(
    session: AsyncSession,
) -> None:
    t1, t2, keep = (f"t-{secrets.token_hex(4)}" for _ in range(3))
    for t in (t1, t2, keep):
        await _seed(session, t)
    await repo.delete_threads(session, [t1, t2])
    assert await _counts(session, t1) == NONE
    assert await _counts(session, t2) == NONE
    assert await _counts(session, keep) == ONE


async def test_delete_threads_with_no_threads_is_a_noop(session: AsyncSession) -> None:
    keep = f"t-{secrets.token_hex(4)}"
    await _seed(session, keep)
    await repo.delete_threads(session, [])
    assert await _counts(session, keep) == ONE


async def test_delete_threads_accepts_more_thread_ids_than_postgres_has_parameters(
    session: AsyncSession,
) -> None:
    keep, gone = (f"t-{secrets.token_hex(4)}" for _ in range(2))
    await _seed(session, keep)
    await _seed(session, gone)
    await repo.delete_threads(session, [f"absent-{i}" for i in range(70_000)] + [gone])
    assert await _counts(session, gone) == NONE
    assert await _counts(session, keep) == ONE


async def test_old_tables_do_not_exist_at_head(session: AsyncSession) -> None:
    for table in ("langgraph_checkpoints", "langgraph_checkpoint_writes"):
        assert (await session.scalar(text("SELECT to_regclass(:t)"), {"t": table})) is None


async def test_delete_threads_clears_all_three_saver_tables_for_those_threads_only(
    session: AsyncSession,
) -> None:
    gone, keep = (f"t-{secrets.token_hex(4)}" for _ in range(2))
    for t in (gone, keep):
        await _seed(session, t)
    await repo.delete_threads(session, [gone])
    assert await _counts(session, gone) == NONE
    assert await _counts(session, keep) == ONE


async def test_delete_threads_runs_in_the_callers_transaction(engine: AsyncEngine) -> None:
    t = f"t-{secrets.token_hex(4)}"
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            await _seed(s, t)
            await s.commit()
        async with sm() as s:
            await repo.delete_threads(s, [t])
            await s.rollback()
        async with sm() as s:
            assert await _counts(s, t) == ONE
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
