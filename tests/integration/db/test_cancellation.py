"""A query cancelled by asyncio.wait_for leaves the session usable after rollback.

src/api/admin_tools.py bounds its key lookup with ``asyncio.wait_for(..., 3.0)``
and then rolls the session back. This pins that a cancellation mid-query does
not poison the session, leak a pooled connection, or leave the statement
running on the server.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

pytestmark = pytest.mark.integration


async def test_cancelled_query_then_rollback_leaves_session_and_pool_usable(
    engine: AsyncEngine,
) -> None:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(session.scalar(text("SELECT pg_sleep(5)")), 0.2)
        await session.rollback()
        assert (await session.execute(text("SELECT 1"))).scalar() == 1

    async with sessionmaker() as check:
        still_sleeping = (
            await check.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE query = 'SELECT pg_sleep(5)' AND state = 'active'"
                )
            )
        ).scalar()
    assert still_sleeping == 0
