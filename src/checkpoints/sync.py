"""Deploy guard: active runs whose newest checkpoint exists only in the old tables.

Compares newest checkpoint ids (time ordered) per thread and namespace between the old
(``langgraph_checkpoints``) and official (``checkpoints``) tables. Reads ids only.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.repos import checkpoints as checkpoints_repo
from src.db.repos.checkpoints import UnsyncedRun


async def find_unsynced(sessionmaker: async_sessionmaker[AsyncSession]) -> list[UnsyncedRun]:
    async with sessionmaker() as session:
        return await checkpoints_repo.unsynced_active_runs(session)
