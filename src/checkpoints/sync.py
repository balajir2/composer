"""Old-table bridge: the deploy guard and copy-on-resume.

Both compare newest checkpoint ids (time-ordered UUIDv6 strings, compared with ``COLLATE "C"``)
per thread and namespace between the old (``langgraph_checkpoints``) and official (``checkpoints``)
tables, through the same SQL in ``checkpoints_repo``. They read ids only.

- ``find_unsynced``: active runs whose newest checkpoint is only (or newer) in the old tables; the
  deploy guard refuses while any exists.
- ``bring_forward``: safety net for a run that paused on a draining old instance after the guard
  ran. ``LangGraphExecutor.resume`` copies the thread old -> new before it loads the checkpoint.
  Removed with the old tables in phase 7.
"""

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.checkpoints.copy import copy_thread
from src.checkpoints.legacy import LegacyCheckpointSaver
from src.db.repos import checkpoints as checkpoints_repo
from src.db.repos.checkpoints import UnsyncedRun

logger = logging.getLogger(__name__)


async def find_unsynced(sessionmaker: async_sessionmaker[AsyncSession]) -> list[UnsyncedRun]:
    async with sessionmaker() as session:
        return await checkpoints_repo.unsynced_active_runs(session)


async def bring_forward(
    checkpointer: BaseCheckpointSaver[Any],
    sessionmaker: async_sessionmaker[AsyncSession],
    thread_id: str,
) -> bool:
    """Copy the thread's old-table checkpoints into the official tables when the old ones lead.

    Only the namespaces whose newest old-table checkpoint is newer than (or missing from) the
    official tables are copied, so a run whose official tables are already ahead (it paused again
    after an earlier copy, or after a rollback and roll-forward) is never overwritten. Returns
    True when something was copied. Savers other than the official one (unit tests use in-memory
    ones) are left alone.

    The session is closed before any saver call: the copy runs without one (short-session rule).
    """
    if not isinstance(checkpointer, AsyncPostgresSaver):
        return False
    async with sessionmaker() as session:
        namespaces = await checkpoints_repo.legacy_ahead_namespaces(session, thread_id)
    if not namespaces:
        return False
    await copy_thread(
        LegacyCheckpointSaver(sessionmaker),
        checkpointer,
        thread_id,
        to="postgres",
        namespaces=namespaces,
    )
    logger.info("checkpoints: brought thread forward from the old tables for thread %s", thread_id)
    return True
