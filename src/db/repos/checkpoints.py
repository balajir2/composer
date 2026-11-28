"""LangGraph checkpoint rows: deleting threads from the official saver's tables in the caller's
transaction."""

from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import SaverCheckpoint, SaverCheckpointBlob, SaverCheckpointWrite
from src.db.repos._sql import in_array

# The official saver's tables that carry a thread_id, in deletion order (writes first). A drift
# test parses the library's own migrations and fails if it ever adds one that is not listed.
SAVER_THREAD_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
_SAVER_MODELS = (SaverCheckpointWrite, SaverCheckpointBlob, SaverCheckpoint)


async def delete_threads(session: AsyncSession, thread_ids: list[str]) -> None:
    """Delete every checkpoint, blob and pending write of ``thread_ids`` from the saver's three
    tables, in the caller's transaction (``adelete_thread`` runs on the saver's own autocommit
    connection and cannot join it; ADR-0036 D7).

    The official saver's ``adelete_thread`` takes one thread, so this clears all of them in the
    same transaction as the execution delete: it commits or rolls back together with it.
    """
    if not thread_ids:
        return
    for model in _SAVER_MODELS:
        await session.execute(
            sa_delete(model)
            .where(in_array(model.thread_id, thread_ids))
            .execution_options(synchronize_session=False)
        )
