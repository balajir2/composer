import secrets

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
