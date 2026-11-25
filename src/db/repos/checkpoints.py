"""LangGraph checkpoint rows (phase 5 replaces this with the saver's own delete)."""

from dataclasses import dataclass

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LangGraphCheckpoint, LangGraphCheckpointWrite, WorkflowExecution
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


@dataclass(frozen=True)
class LegacyCheckpointRow:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: str | None
    checkpoint: bytes
    metadata: bytes


@dataclass(frozen=True)
class LegacyWriteRow:
    task_id: str
    idx: int
    channel: str
    value: bytes


def _to_row(m: LangGraphCheckpoint) -> LegacyCheckpointRow:
    return LegacyCheckpointRow(
        thread_id=m.thread_id,
        checkpoint_ns=m.checkpoint_ns,
        checkpoint_id=m.checkpoint_id,
        parent_checkpoint_id=m.parent_checkpoint_id,
        checkpoint=bytes(m.checkpoint),
        metadata=bytes(m.metadata_),
    )


async def legacy_get(
    session: AsyncSession, thread_id: str, checkpoint_ns: str, checkpoint_id: str | None
) -> LegacyCheckpointRow | None:
    """One checkpoint; with no id, the newest by ``checkpoint_id`` (not ``created_at``, whose
    millisecond precision can tie within one run)."""
    stmt = select(LangGraphCheckpoint).where(
        LangGraphCheckpoint.thread_id == thread_id,
        LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
    )
    if checkpoint_id:
        stmt = stmt.where(LangGraphCheckpoint.checkpoint_id == checkpoint_id)
    else:
        stmt = stmt.order_by(LangGraphCheckpoint.checkpoint_id.desc()).limit(1)
    row = (await session.execute(stmt)).scalars().first()
    return _to_row(row) if row is not None else None


async def legacy_list(
    session: AsyncSession,
    thread_id: str,
    checkpoint_ns: str,
    *,
    before_id: str | None,
    limit: int | None,
) -> list[LegacyCheckpointRow]:
    """Checkpoints of one thread and namespace, newest ``checkpoint_id`` first."""
    stmt = select(LangGraphCheckpoint).where(
        LangGraphCheckpoint.thread_id == thread_id,
        LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
    )
    if before_id is not None:
        stmt = stmt.where(LangGraphCheckpoint.checkpoint_id < before_id)
    stmt = stmt.order_by(LangGraphCheckpoint.checkpoint_id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return [_to_row(m) for m in (await session.execute(stmt)).scalars()]


async def legacy_writes(
    session: AsyncSession, thread_id: str, checkpoint_ns: str, checkpoint_id: str
) -> list[LegacyWriteRow]:
    stmt = (
        select(LangGraphCheckpointWrite)
        .where(
            LangGraphCheckpointWrite.thread_id == thread_id,
            LangGraphCheckpointWrite.checkpoint_ns == checkpoint_ns,
            LangGraphCheckpointWrite.checkpoint_id == checkpoint_id,
        )
        .order_by(LangGraphCheckpointWrite.task_id, LangGraphCheckpointWrite.idx)
    )
    return [
        LegacyWriteRow(m.task_id, m.idx, m.channel, bytes(m.value))
        for m in (await session.execute(stmt)).scalars()
    ]


async def legacy_upsert_checkpoint(session: AsyncSession, row: LegacyCheckpointRow) -> None:
    ins = pg_insert(LangGraphCheckpoint).values(
        thread_id=row.thread_id,
        checkpoint_ns=row.checkpoint_ns,
        checkpoint_id=row.checkpoint_id,
        parent_checkpoint_id=row.parent_checkpoint_id,
        checkpoint=row.checkpoint,
        metadata=row.metadata,
    )
    await session.execute(
        ins.on_conflict_do_update(
            index_elements=["thread_id", "checkpoint_ns", "checkpoint_id"],
            set_={
                "parent_checkpoint_id": ins.excluded.parent_checkpoint_id,
                "checkpoint": ins.excluded.checkpoint,
                "metadata": ins.excluded.metadata,
            },
        )
    )


async def legacy_upsert_writes(
    session: AsyncSession,
    thread_id: str,
    checkpoint_ns: str,
    checkpoint_id: str,
    task_id: str,
    writes: list[tuple[int, str, bytes]],
) -> None:
    """Upsert ``(idx, channel, value)`` rows: a repeated (task, idx) is replaced (last wins)."""
    for idx, channel, value in writes:
        ins = pg_insert(LangGraphCheckpointWrite).values(
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            task_id=task_id,
            idx=idx,
            channel=channel,
            value=value,
        )
        await session.execute(
            ins.on_conflict_do_update(
                index_elements=["thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"],
                set_={"channel": ins.excluded.channel, "value": ins.excluded.value},
            )
        )


async def legacy_namespaces(session: AsyncSession, thread_id: str) -> list[str]:
    stmt = (
        select(LangGraphCheckpoint.checkpoint_ns)
        .where(LangGraphCheckpoint.thread_id == thread_id)
        .distinct()
        .order_by(LangGraphCheckpoint.checkpoint_ns)
    )
    return list((await session.execute(stmt)).scalars())


async def saver_namespaces(session: AsyncSession, thread_id: str) -> list[str]:
    """Namespaces the official saver's ``checkpoints`` table holds for a thread."""
    rows = await session.execute(
        text("SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id = :t ORDER BY 1"),
        {"t": thread_id},
    )
    return [str(r[0]) for r in rows]


async def waiting_threads(session: AsyncSession) -> list[tuple[str, str]]:
    """``(execution_id, thread_id)`` of every ``waiting_approval`` execution."""
    rows = await session.execute(
        select(WorkflowExecution.id, WorkflowExecution.thread_id)
        .where(WorkflowExecution.status == "waiting_approval")
        .order_by(WorkflowExecution.id)
    )
    return [(str(r[0]), str(r[1])) for r in rows]
