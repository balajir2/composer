"""LangGraph checkpoint rows: deletes across every checkpoint table, and the old tables' SQL."""

from dataclasses import dataclass

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.execution_status import ACTIVE_EXECUTION_STATUSES_SORTED
from src.db.models import (
    LangGraphCheckpoint,
    LangGraphCheckpointWrite,
    SaverCheckpoint,
    SaverCheckpointBlob,
    SaverCheckpointWrite,
    WorkflowExecution,
)
from src.db.repos._sql import in_array

ACTIVE_STATUSES = tuple(ACTIVE_EXECUTION_STATUSES_SORTED)


# The official saver's tables that carry a thread_id, in deletion order (writes first). A drift
# test parses the library's own migrations and fails if it ever adds one that is not listed.
SAVER_THREAD_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
_SAVER_MODELS = (SaverCheckpointWrite, SaverCheckpointBlob, SaverCheckpoint)


async def delete_threads(session: AsyncSession, thread_ids: list[str]) -> None:
    """Delete every checkpoint, blob and pending write of ``thread_ids`` from all five tables.

    The official saver's ``adelete_thread`` takes one thread and runs on the saver's own autocommit
    connection, so it cannot join the SQLAlchemy transaction that deletes the execution rows
    (decision D7). This clears the saver's three tables and the old two (history until phase 7)
    in the caller's transaction, so it commits or rolls back together with the execution delete.
    """
    if not thread_ids:
        return
    for model in (
        *_SAVER_MODELS,
        LangGraphCheckpointWrite,
        LangGraphCheckpoint,
    ):
        await session.execute(
            sa_delete(model)
            .where(in_array(model.thread_id, thread_ids))
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


@dataclass(frozen=True)
class UnsyncedRun:
    execution_id: str
    status: str
    thread_id: str


# Newest checkpoint id per (thread, namespace) in each table set. Ids are time-ordered UUIDv6
# strings, so the newest is the lexically largest; COLLATE "C" keeps the order independent of the
# database collation (which can ignore dashes).
_NEW_NEWEST = """
        SELECT thread_id, checkpoint_ns, max(checkpoint_id COLLATE "C") AS cid
        FROM checkpoints GROUP BY thread_id, checkpoint_ns
"""
# Before Alembic 0004 the official tables do not exist (the deploy guard runs before the
# migration): the official side is then empty, so every old-table checkpoint is "ahead".
_NEW_NEWEST_ABSENT = """
        SELECT NULL::text AS thread_id, NULL::text AS checkpoint_ns, NULL::text AS cid
        WHERE false
"""


def _unsynced_from(saver_tables_exist: bool) -> str:
    return f"""
    FROM (
        SELECT thread_id, checkpoint_ns, max(checkpoint_id COLLATE "C") AS cid
        FROM langgraph_checkpoints GROUP BY thread_id, checkpoint_ns
    ) old
    LEFT JOIN ({_NEW_NEWEST if saver_tables_exist else _NEW_NEWEST_ABSENT}) nw
      ON nw.thread_id = old.thread_id AND nw.checkpoint_ns = old.checkpoint_ns
"""


async def _unsynced_from_for(session: AsyncSession) -> str:
    exists = (await session.execute(text("SELECT to_regclass('checkpoints') IS NOT NULL"))).scalar()
    return _unsynced_from(bool(exists))


_OLD_AHEAD = "(nw.cid IS NULL OR nw.cid < old.cid)"


async def unsynced_active_runs(session: AsyncSession) -> list[UnsyncedRun]:
    """Active executions with a namespace whose newest old-table checkpoint is missing from, or
    newer than, the official tables. Ordered by execution id."""
    from_ = await _unsynced_from_for(session)
    rows = await session.execute(
        text(
            "SELECT DISTINCT e.id, e.status, e.thread_id "
            + from_
            + " JOIN workflow_executions e ON e.thread_id = old.thread_id"
            + f" WHERE e.status = ANY(:active) AND {_OLD_AHEAD} ORDER BY e.id"
        ),
        {"active": list(ACTIVE_STATUSES)},
    )
    return [UnsyncedRun(str(r[0]), str(r[1]), str(r[2])) for r in rows]


async def legacy_ahead_namespaces(session: AsyncSession, thread_id: str) -> list[str]:
    """Namespaces of the thread whose newest checkpoint is newer in (or only present in) the old
    tables. Same comparison as the deploy guard: newest ids, ``COLLATE "C"``."""
    from_ = await _unsynced_from_for(session)
    rows = await session.execute(
        text(
            "SELECT old.checkpoint_ns "
            + from_
            + f" WHERE old.thread_id = :t AND {_OLD_AHEAD} ORDER BY old.checkpoint_ns"
        ),
        {"t": thread_id},
    )
    return [str(r[0]) for r in rows]


async def legacy_ahead(session: AsyncSession, thread_id: str) -> bool:
    """True when any namespace of the thread is newer (or only present) in the old tables."""
    from_ = await _unsynced_from_for(session)
    row = await session.execute(
        text("SELECT 1 " + from_ + f" WHERE old.thread_id = :t AND {_OLD_AHEAD} LIMIT 1"),
        {"t": thread_id},
    )
    return row.first() is not None


async def active_status_counts(session: AsyncSession) -> dict[str, int]:
    """Executions per active status (zero for statuses with none)."""
    counts = dict.fromkeys(ACTIVE_STATUSES, 0)
    rows = await session.execute(
        select(WorkflowExecution.status, func.count())
        .where(WorkflowExecution.status.in_(ACTIVE_STATUSES))
        .group_by(WorkflowExecution.status)
    )
    for status, n in rows:
        counts[str(status)] = int(n)
    return counts
