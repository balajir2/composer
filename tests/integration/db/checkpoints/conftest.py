"""Fixtures for the legacy (old-table) checkpoint saver tests."""

import secrets
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.checkpoints.legacy import LegacyCheckpointSaver


@pytest.fixture
def sessionmaker_(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    return app.state.db_sessionmaker


@pytest.fixture
def legacy(sessionmaker_: async_sessionmaker[AsyncSession]) -> LegacyCheckpointSaver:
    return LegacyCheckpointSaver(sessionmaker_)


@pytest_asyncio.fixture
async def threads(sessionmaker_: async_sessionmaker[AsyncSession]) -> AsyncIterator[list[str]]:
    """Unique thread ids; every row of every id handed out is deleted afterwards."""
    handed: list[str] = []
    yield handed
    async with sessionmaker_() as s:
        for table in ("langgraph_checkpoint_writes", "langgraph_checkpoints"):
            await s.execute(
                text(f"DELETE FROM {table} WHERE thread_id = ANY(:t)"),
                {"t": handed},
            )
        await s.commit()


@pytest.fixture
def new_thread(threads: list[str]) -> Callable[[], str]:
    def make() -> str:
        tid = f"legacy-{secrets.token_hex(6)}"
        threads.append(tid)
        return tid

    return make
