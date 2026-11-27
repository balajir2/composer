"""Repository operations and immutable records for execution attempt history."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_referenced_row_missing
from src.db.models import AttemptResult, AttemptTriggerKind, ExecutionAttempt, WorkflowExecution


@dataclass(frozen=True)
class AttemptRecord:
    id: str
    execution_id: str
    ordinal: int
    started_at: datetime
    ended_at: datetime | None
    result: AttemptResult
    failure_code: str | None
    lease_owner: str
    lease_expires_at: datetime
    trigger_kind: AttemptTriggerKind
    recovery_source_attempt_id: str | None
    approval_id: str | None
    actor_user_id: str | None
    reason_ref: str | None


def _record(row: ExecutionAttempt) -> AttemptRecord:
    return AttemptRecord(
        id=row.id,
        execution_id=row.execution_id,
        ordinal=row.ordinal,
        started_at=row.started_at,
        ended_at=row.ended_at,
        result=AttemptResult(row.result),
        failure_code=row.failure_code,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        trigger_kind=AttemptTriggerKind(row.trigger_kind),
        recovery_source_attempt_id=row.recovery_source_attempt_id,
        approval_id=row.approval_id,
        actor_user_id=row.actor_user_id,
        reason_ref=row.reason_ref,
    )


async def open_for_claim(
    session: AsyncSession,
    *,
    execution_id: str,
    started_at: datetime,
    lease_owner: str,
    lease_expires_at: datetime,
    trigger_kind: AttemptTriggerKind,
    approval_id: str | None,
    actor_user_id: str | None,
    recovery_source_attempt_id: str | None = None,
    reason_ref: str | None = None,
) -> AttemptRecord:
    """Insert the next history ordinal; caller holds the execution row lock and transaction."""
    ordinal = (
        await session.scalar(
            select(func.coalesce(func.max(ExecutionAttempt.ordinal), 0) + 1).where(
                ExecutionAttempt.execution_id == execution_id
            )
        )
    ) or 1
    row = ExecutionAttempt(
        execution_id=execution_id,
        ordinal=ordinal,
        started_at=started_at,
        result=AttemptResult.RUNNING,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        trigger_kind=trigger_kind,
        approval_id=approval_id,
        actor_user_id=actor_user_id,
        recovery_source_attempt_id=recovery_source_attempt_id,
        reason_ref=reason_ref,
    )
    await add_or_referenced_row_missing(session, row)
    return _record(row)


async def close_open(
    session: AsyncSession,
    *,
    execution_id: str,
    attempt_id: str,
    result: AttemptResult,
    failure_code: str | None,
    ended_at: datetime,
) -> bool:
    """Close the matching open attempt once, leaving the lease snapshot unchanged."""
    won = await session.scalar(
        update(ExecutionAttempt)
        .where(
            ExecutionAttempt.id == attempt_id,
            ExecutionAttempt.execution_id == execution_id,
            ExecutionAttempt.ended_at.is_(None),
        )
        .values(result=result, failure_code=failure_code, ended_at=ended_at)
        .returning(ExecutionAttempt.id)
        .execution_options(synchronize_session=False)
    )
    return won is not None


RECONCILED_FAILURE_CODE = "closed_by_reconciliation"


async def close_open_for_terminal_executions(session: AsyncSession, *, now: datetime) -> int:
    """Close every open attempt whose execution is already terminal; return how many.

    A rolled-back (0004) image cancels or lease-fails executions without closing attempts, which
    would show them "In progress" forever. One guarded UPDATE: the attempt must still be open and
    the execution terminal when the statement runs, so a concurrent worker close wins and a
    re-run matches nothing. The end time is the execution's ``completed_at`` (else ``now``),
    never before the attempt's start, so the ended_at ordering check holds.
    """
    ended_at = func.greatest(
        func.coalesce(WorkflowExecution.completed_at, now), ExecutionAttempt.started_at
    )
    closed = await session.scalars(
        update(ExecutionAttempt)
        .where(
            ExecutionAttempt.execution_id == WorkflowExecution.id,
            ExecutionAttempt.ended_at.is_(None),
            WorkflowExecution.status.in_(("completed", "failed", "canceled")),
        )
        .values(
            result=WorkflowExecution.status,
            failure_code=RECONCILED_FAILURE_CODE,
            ended_at=ended_at,
        )
        .returning(ExecutionAttempt.id)
        .execution_options(synchronize_session=False)
    )
    return len(closed.all())


async def get(session: AsyncSession, attempt_id: str) -> AttemptRecord | None:
    row = await session.scalar(
        select(ExecutionAttempt)
        .where(ExecutionAttempt.id == attempt_id)
        .execution_options(populate_existing=True)
    )
    return None if row is None else _record(row)


async def has_history(session: AsyncSession, execution_id: str) -> bool:
    return (
        await session.scalar(
            select(ExecutionAttempt.id)
            .where(ExecutionAttempt.execution_id == execution_id)
            .limit(1)
        )
    ) is not None


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if offset < 0:
        raise ValueError("offset must be non-negative")


async def list_page(
    session: AsyncSession, *, execution_id: str, limit: int, offset: int
) -> tuple[list[AttemptRecord], int]:
    """Return one execution's history in ordinal order, plus its total count."""
    _validate_page(limit, offset)
    pred = ExecutionAttempt.execution_id == execution_id
    total = await session.scalar(select(func.count()).select_from(ExecutionAttempt).where(pred))
    rows = await session.scalars(
        select(ExecutionAttempt)
        .where(pred)
        .order_by(ExecutionAttempt.ordinal.asc(), ExecutionAttempt.id.asc())
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    return [_record(row) for row in rows], total or 0


async def list_operational_page(
    session: AsyncSession,
    *,
    result: AttemptResult | None,
    started_after: datetime | None,
    started_before: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[AttemptRecord], int]:
    """Return globally ordered operational history, optionally filtered by result and time."""
    _validate_page(limit, offset)
    preds: list[ColumnElement[bool]] = []
    if result is not None:
        preds.append(ExecutionAttempt.result == result)
    if started_after is not None:
        preds.append(ExecutionAttempt.started_at >= started_after)
    if started_before is not None:
        preds.append(ExecutionAttempt.started_at <= started_before)
    total = await session.scalar(select(func.count()).select_from(ExecutionAttempt).where(*preds))
    rows = await session.scalars(
        select(ExecutionAttempt)
        .where(*preds)
        .order_by(ExecutionAttempt.started_at.desc(), ExecutionAttempt.id.desc())
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    return [_record(row) for row in rows], total or 0


__all__ = [
    "AttemptRecord",
    "close_open",
    "get",
    "has_history",
    "list_operational_page",
    "list_page",
    "open_for_claim",
]
