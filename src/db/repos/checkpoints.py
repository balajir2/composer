"""LangGraph checkpoint rows (phase 5 replaces this with the saver's own delete)."""

from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LangGraphCheckpoint, LangGraphCheckpointWrite
from src.db.repos._sql import in_array


async def delete_threads(session: AsyncSession, thread_ids: list[str]) -> None:
    """Delete every checkpoint and pending write of ``thread_ids``.

    Writes go first, then checkpoints, as the Prisma code did. Runs in the caller's
    transaction, so it commits or rolls back together with the execution delete.
    """
    if not thread_ids:
        return
    await session.execute(
        sa_delete(LangGraphCheckpointWrite)
        .where(in_array(LangGraphCheckpointWrite.thread_id, thread_ids))
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        sa_delete(LangGraphCheckpoint)
        .where(in_array(LangGraphCheckpoint.thread_id, thread_ids))
        .execution_options(synchronize_session=False)
    )
