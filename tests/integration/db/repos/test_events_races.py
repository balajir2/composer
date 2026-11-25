"""Concurrent event appends: seqs are exactly 1..N, none lost (decision D2)."""

import asyncio
import secrets
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import ExecutionEvent
from src.db.repos import events as repo

pytestmark = pytest.mark.integration

REPEATS = 10
N = 20


@pytest.fixture
async def sm(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield async_sessionmaker(engine, expire_on_commit=False)


async def _append(sm: async_sessionmaker[AsyncSession], eid: str, i: int) -> int:
    async with sm() as s:
        seq = await repo.append(s, execution_id=eid, type="t", payload={"i": i})
        await s.commit()
        return seq


async def test_concurrent_appends_produce_exactly_one_to_n(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(REPEATS):
        eid = f"ex-{secrets.token_hex(6)}"
        try:
            seqs = await asyncio.gather(*(_append(sm, eid, i) for i in range(N)))
            assert sorted(seqs) == list(range(1, N + 1))
            async with sm() as s:
                stored = (
                    await s.scalars(
                        select(ExecutionEvent.seq).where(ExecutionEvent.execution_id == eid)
                    )
                ).all()
            assert sorted(stored) == list(range(1, N + 1))
        finally:
            async with sm() as s:
                await s.execute(delete(ExecutionEvent).where(ExecutionEvent.execution_id == eid))
                await s.commit()


async def test_concurrent_appends_to_two_executions_are_independent(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    a, b = f"ex-{secrets.token_hex(6)}", f"ex-{secrets.token_hex(6)}"
    try:
        ra, rb = await asyncio.gather(
            asyncio.gather(*(_append(sm, a, i) for i in range(10))),
            asyncio.gather(*(_append(sm, b, i) for i in range(10))),
        )
        assert sorted(ra) == list(range(1, 11))
        assert sorted(rb) == list(range(1, 11))
    finally:
        async with sm() as s:
            await s.execute(delete(ExecutionEvent).where(ExecutionEvent.execution_id.in_([a, b])))
            await s.commit()
