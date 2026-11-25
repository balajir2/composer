"""``ExecutionEventListener`` against a real Postgres: targeted delivery of a real NOTIFY sent
through the SQLAlchemy pool, and a LISTEN connection that exists only while someone watches."""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import runtime
from src.db.url import direct_endpoint_url, to_libpq_url, to_sqlalchemy_url
from src.engine.events_notify import notify_execution_event
from src.engine.notifications import LISTENER_APPLICATION_NAME, ExecutionEventListener

pytestmark = pytest.mark.integration


@pytest.fixture
def conninfo() -> str:
    # The local test database has no -pooler host, so the derived URL is the configured one.
    url = to_libpq_url(os.environ["TEST_DATABASE_URL"])
    assert direct_endpoint_url(url) == url
    return direct_endpoint_url(url)


@pytest_asyncio.fixture
async def sm() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_sqlalchemy_url(os.environ["TEST_DATABASE_URL"]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    runtime.register_sessionmaker(maker)
    try:
        yield maker
    finally:
        runtime.unregister_sessionmaker(maker)
        await engine.dispose()


async def _listen_count(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND query ILIKE 'LISTEN%' "
                    "AND application_name = :name"
                ),
                {"name": LISTENER_APPLICATION_NAME},
            )
            or 0
        )


async def _until_count(maker: async_sessionmaker[AsyncSession], want: int, timeout: float) -> int:
    count = await _listen_count(maker)
    deadline = asyncio.get_running_loop().time() + timeout
    while count != want and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
        count = await _listen_count(maker)
    return count


async def test_a_notify_wakes_only_the_matching_execution(
    conninfo: str, sm: async_sessionmaker[AsyncSession]
) -> None:
    listener = ExecutionEventListener(conninfo, idle_close_seconds=0.2)
    try:
        with listener.subscribe("e1") as e1, listener.subscribe("e2") as e2:
            assert await _until_count(sm, 1, 5.0) == 1
            await asyncio.wait_for(e1.wait(), 2.0)  # the connect wake-all
            await asyncio.wait_for(e2.wait(), 2.0)
            e1.clear()
            e2.clear()

            await notify_execution_event("e1", seq=1)
            await asyncio.wait_for(e1.wait(), 2.0)
            await asyncio.sleep(0.3)
            assert not e2.is_set()
    finally:
        await listener.aclose()


async def test_listen_connection_exists_only_while_someone_watches(
    conninfo: str, sm: async_sessionmaker[AsyncSession]
) -> None:
    listener = ExecutionEventListener(conninfo, idle_close_seconds=0.2)
    try:
        assert await _listen_count(sm) == 0
        with listener.subscribe("e1"):
            assert await _until_count(sm, 1, 5.0) == 1
        assert await _until_count(sm, 0, 5.0) == 0
    finally:
        await listener.aclose()


async def test_aclose_closes_the_listen_connection_without_waiting_for_the_idle_timer(
    conninfo: str, sm: async_sessionmaker[AsyncSession]
) -> None:
    listener = ExecutionEventListener(conninfo, idle_close_seconds=3600.0)
    sub = listener.subscribe("e1")
    sub.__enter__()
    try:
        assert await _until_count(sm, 1, 5.0) == 1
    finally:
        await listener.aclose()
        sub.__exit__(None, None, None)
    assert await _until_count(sm, 0, 3.0) == 0
