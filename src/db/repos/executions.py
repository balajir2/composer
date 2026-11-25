"""Workflow executions: rows, guarded status transitions, the work claim and sweep scans.

Every ``*_if_*`` function is ONE ``UPDATE ... WHERE <guard> RETURNING id`` and returns
True iff a row came back, so a concurrent writer can never be overwritten. Never
read-then-write. Callers own the transaction.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict

from sqlalchemy import JSON, ColumnElement, cast, func, literal, select, text, type_coerce
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.execution_status import ACTIVE_EXECUTION_STATUSES_SORTED
from src.db.errors import add_or_already_exists
from src.db.models import (
    AttemptResult,
    AttemptTriggerKind,
    ExecutionAttempt,
    ExecutionEvent,
    WorkflowExecution,
)
from src.db.repos import checkpoints as checkpoints_repo
from src.db.repos import execution_attempts as execution_attempts_repo
from src.db.repos._sql import in_array

ACTIVE_STATUSES: tuple[str, ...] = tuple(ACTIVE_EXECUTION_STATUSES_SORTED)


@dataclass(frozen=True)
class ExecutionRecord:
    id: str
    workflow_id: str
    user_id: str | None
    status: str
    current_node_id: str | None
    node_results: Any
    variables: Any
    input: Any
    output: Any
    error: str | None
    failure_code: str | None
    started_at: datetime
    completed_at: datetime | None
    thread_id: str
    original_owner_email: str | None
    idempotency_key: str | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    running_since: datetime | None
    delivery_attempts: int


@dataclass(frozen=True)
class ClaimedExecution:
    id: str
    variables: Any
    attempt_id: str
    attempt_ordinal: int


@dataclass(frozen=True)
class ExecutionRef:
    id: str
    thread_id: str
    status: str


class ExecutionInsert(TypedDict):
    """Values for the migration writer; unknown keys are a type error."""

    id: str
    workflow_id: str
    status: str
    thread_id: str
    node_results: Any
    variables: Any
    user_id: NotRequired[str | None]
    original_owner_email: NotRequired[str | None]
    current_node_id: NotRequired[str | None]
    input: NotRequired[Any]
    output: NotRequired[Any]
    error: NotRequired[str | None]


def _record(r: WorkflowExecution) -> ExecutionRecord:
    return ExecutionRecord(
        id=r.id,
        workflow_id=r.workflow_id,
        user_id=r.user_id,
        status=r.status,
        current_node_id=r.current_node_id,
        node_results=r.node_results,
        variables=r.variables,
        input=r.input,
        output=r.output,
        error=r.error,
        failure_code=r.failure_code,
        started_at=r.started_at,
        completed_at=r.completed_at,
        thread_id=r.thread_id,
        original_owner_email=r.original_owner_email,
        idempotency_key=r.idempotency_key,
        lease_owner=r.lease_owner,
        lease_expires_at=r.lease_expires_at,
        running_since=r.running_since,
        delivery_attempts=r.delivery_attempts,
    )


def _json(value: Any) -> Any:
    """Store an absent value as JSON ``null`` (what Prisma's ``Json(None)`` did), not SQL NULL."""
    return JSON.NULL if value is None else value


async def create(
    session: AsyncSession,
    *,
    workflow_id: str,
    user_id: str | None,
    input: Any,
    thread_id: str,
    idempotency_key: str | None = None,
) -> ExecutionRecord:
    """Insert a ``queued`` execution. ``AlreadyExists`` on a duplicate (user, key) or thread."""
    row = WorkflowExecution(
        workflow_id=workflow_id,
        user_id=user_id,
        status="queued",
        node_results={},
        variables={},
        input=_json(input),
        thread_id=thread_id,
        idempotency_key=idempotency_key,
        delivery_attempts=0,
    )
    await add_or_already_exists(session, row)
    await session.refresh(row)
    return _record(row)


async def get(session: AsyncSession, execution_id: str) -> ExecutionRecord | None:
    """The execution, read fresh from the database."""
    row = await session.scalar(
        select(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id)
        .execution_options(populate_existing=True)
    )
    return None if row is None else _record(row)


async def get_status(session: AsyncSession, execution_id: str) -> str | None:
    return await session.scalar(
        select(WorkflowExecution.status).where(WorkflowExecution.id == execution_id)
    )


async def find_by_idempotency_key(
    session: AsyncSession, *, workflow_id: str, user_id: str, key: str
) -> ExecutionRecord | None:
    """The caller's own execution for this workflow and key (keys are scoped per user)."""
    row = await session.scalar(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.workflow_id == workflow_id,
            WorkflowExecution.user_id == user_id,
            WorkflowExecution.idempotency_key == key,
        )
        .execution_options(populate_existing=True)
    )
    return None if row is None else _record(row)


async def list_page(
    session: AsyncSession,
    *,
    owner_id: str | None,
    workflow_id: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[ExecutionRecord], int]:
    """A page ordered by ``started_at DESC`` (no tiebreak, as today) and the total count."""
    preds: list[ColumnElement[bool]] = []
    if owner_id is not None:
        preds.append(WorkflowExecution.user_id == owner_id)
    if workflow_id is not None:
        preds.append(WorkflowExecution.workflow_id == workflow_id)
    if status is not None:
        preds.append(WorkflowExecution.status == status)
    total = await session.scalar(select(func.count()).select_from(WorkflowExecution).where(*preds))
    rows = await session.scalars(
        select(WorkflowExecution)
        .where(*preds)
        .order_by(WorkflowExecution.started_at.desc())
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    return [_record(r) for r in rows], total or 0


async def list_refs(
    session: AsyncSession, *, ids: list[str] | None, owner_id: str | None
) -> list[ExecutionRef]:
    """Id, thread and status of executions (``ids`` None = all), optionally of one owner."""
    stmt = select(WorkflowExecution.id, WorkflowExecution.thread_id, WorkflowExecution.status)
    if ids is not None:
        stmt = stmt.where(in_array(WorkflowExecution.id, ids))
    if owner_id is not None:
        stmt = stmt.where(WorkflowExecution.user_id == owner_id)
    return [ExecutionRef(r.id, r.thread_id, r.status) for r in await session.execute(stmt)]


async def delete_terminal(session: AsyncSession, ids: list[str]) -> list[ExecutionRef]:
    """Delete the non-active executions among ``ids`` with their events and checkpoints.

    One transaction (the caller's): the executions go first with an exact status guard,
    then the checkpoint writes, checkpoints and events of exactly the rows deleted.
    Approvals cascade in the database. Returns what was deleted.
    """
    if not ids:
        return []
    result = await session.execute(
        sa_delete(WorkflowExecution)
        .where(
            in_array(WorkflowExecution.id, ids), WorkflowExecution.status.not_in(ACTIVE_STATUSES)
        )
        .returning(WorkflowExecution.id, WorkflowExecution.thread_id, WorkflowExecution.status)
        .execution_options(synchronize_session=False)
    )
    deleted = [ExecutionRef(r.id, r.thread_id, r.status) for r in result]
    if deleted:
        await checkpoints_repo.delete_threads(session, [d.thread_id for d in deleted])
        await session.execute(
            sa_delete(ExecutionEvent)
            .where(in_array(ExecutionEvent.execution_id, [d.id for d in deleted]))
            .execution_options(synchronize_session=False)
        )
    return deleted


_CLAIMABLE: dict[str, tuple[str, ...]] = {"run": ("queued",), "resume": ("running",)}


async def claim(
    session: AsyncSession,
    execution_id: str,
    *,
    kind: Literal["run", "resume"],
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
) -> ClaimedExecution | None:
    """Take the lease on an unleased execution of the kind's status, or None.

    One statement: the row is locked with ``FOR UPDATE SKIP LOCKED`` in a subquery and the
    eligibility is re-checked by the outer ``UPDATE``, so exactly one concurrent caller wins
    and nobody waits. Must be the first statements of the caller's transaction (the
    ``SET LOCAL`` timeouts last until it ends). The caller must wrap the transaction in
    ``asyncio.timeout(10)``, commit before running anything, and know that
    ``SET LOCAL statement_timeout`` (10 s) applies to every later statement of the same
    transaction.
    """
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(text("SET LOCAL statement_timeout = '10s'"))
    eligible = (
        WorkflowExecution.id == execution_id,
        WorkflowExecution.status.in_(_CLAIMABLE[kind]),
        WorkflowExecution.lease_expires_at.is_(None),
        WorkflowExecution.lease_owner.is_(None),
    )
    locked = (
        select(WorkflowExecution.id).where(*eligible).with_for_update(skip_locked=True).limit(1)
    )
    row = (
        await session.execute(
            sa_update(WorkflowExecution)
            .where(WorkflowExecution.id == locked.scalar_subquery(), *eligible)
            .values(
                status="running",
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                running_since=now,
                delivery_attempts=WorkflowExecution.delivery_attempts + 1,
            )
            .returning(
                WorkflowExecution.id,
                WorkflowExecution.variables,
                WorkflowExecution.pending_attempt_approval_id,
            )
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if row is None:
        return None
    attempt = await execution_attempts_repo.open_for_claim(
        session,
        execution_id=row.id,
        started_at=now,
        lease_owner=worker_id,
        lease_expires_at=lease_expires_at,
        trigger_kind=AttemptTriggerKind(kind),
        approval_id=row.pending_attempt_approval_id,
        actor_user_id=None,
    )
    if row.pending_attempt_approval_id is not None:
        await session.execute(
            sa_update(WorkflowExecution)
            .where(WorkflowExecution.id == row.id)
            .values(pending_attempt_approval_id=None)
            .execution_options(synchronize_session=False)
        )
    return ClaimedExecution(
        id=row.id,
        variables=row.variables,
        attempt_id=attempt.id,
        attempt_ordinal=attempt.ordinal,
    )


async def _guarded(
    session: AsyncSession,
    execution_id: str,
    guard: tuple[ColumnElement[bool], ...],
    values: dict[str, Any],
) -> bool:
    won = await session.scalar(
        sa_update(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id, *guard)
        .values(**values)
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    return won is not None


_NO_LEASE: dict[str, Any] = {"lease_owner": None, "lease_expires_at": None, "running_since": None}


async def mark_enqueue_failed_if_queued(
    session: AsyncSession, execution_id: str, *, error: str, now: datetime
) -> bool:
    """G1: queued -> failed after the enqueue raised; a concurrent cancel is left alone."""
    return await _guarded(
        session,
        execution_id,
        (WorkflowExecution.status == "queued",),
        {"status": "failed", "error": error, "completed_at": now},
    )


async def cancel_if_active(
    session: AsyncSession, execution_id: str, *, error: str, now: datetime
) -> bool:
    """G2: queued/running/waiting_approval -> canceled, dropping any lease."""
    return await _guarded(
        session,
        execution_id,
        (WorkflowExecution.status.in_(ACTIVE_STATUSES),),
        {"status": "canceled", "error": error, "completed_at": now, **_NO_LEASE},
    )


def _worker_attempt_guard(attempt_id: str | None) -> ColumnElement[bool]:
    """Fence an outcome to its open attempt; allow old rows without one."""
    open_attempt = select(ExecutionAttempt.id).where(
        ExecutionAttempt.execution_id == WorkflowExecution.id,
        ExecutionAttempt.ended_at.is_(None),
    )
    if attempt_id is not None:
        open_attempt = open_attempt.where(ExecutionAttempt.id == attempt_id)
        return open_attempt.exists()
    return ~open_attempt.exists()


async def _worker_transition(
    session: AsyncSession,
    execution_id: str,
    *,
    attempt_id: str | None,
    values: dict[str, Any],
    result: AttemptResult,
    failure_code: str | None,
    now: datetime,
) -> bool:
    won = await _guarded(
        session,
        execution_id,
        (WorkflowExecution.status == "running", _worker_attempt_guard(attempt_id)),
        values,
    )
    if won and attempt_id is not None:
        closed = await execution_attempts_repo.close_open(
            session,
            execution_id=execution_id,
            attempt_id=attempt_id,
            result=result,
            failure_code=failure_code,
            ended_at=now,
        )
        if not closed:
            raise RuntimeError("guarded worker outcome won but attempt closure lost")
    return won


async def mark_failed_if_running(
    session: AsyncSession,
    execution_id: str,
    *,
    error: str,
    failure_code: str,
    now: datetime,
    attempt_id: str | None = None,
) -> bool:
    """G6/G9: running -> failed, dropping the lease."""
    return await _worker_transition(
        session,
        execution_id,
        attempt_id=attempt_id,
        values={
            "status": "failed",
            "error": error,
            "failure_code": failure_code,
            "completed_at": now,
            **_NO_LEASE,
        },
        result=AttemptResult.FAILED,
        failure_code=failure_code,
        now=now,
    )


async def mark_completed_if_running(
    session: AsyncSession,
    execution_id: str,
    *,
    output: Any,
    variables: Any,
    node_results: Any,
    now: datetime,
    attempt_id: str | None = None,
) -> bool:
    """G7: running -> completed. An absent output is stored as JSON null."""
    return await _worker_transition(
        session,
        execution_id,
        attempt_id=attempt_id,
        values={
            "status": "completed",
            "output": _json(output),
            "variables": variables,
            "node_results": node_results,
            "completed_at": now,
            **_NO_LEASE,
        },
        result=AttemptResult.COMPLETED,
        failure_code=None,
        now=now,
    )


async def mark_waiting_approval_if_running(
    session: AsyncSession,
    execution_id: str,
    *,
    variables: Any,
    now: datetime | None = None,
    attempt_id: str | None = None,
) -> bool:
    """G8: running -> waiting_approval with the pending markers; ``completed_at`` untouched."""
    if attempt_id is not None and now is None:
        raise ValueError("now is required when closing an attempt")
    return await _worker_transition(
        session,
        execution_id,
        attempt_id=attempt_id,
        values={"status": "waiting_approval", "variables": variables, **_NO_LEASE},
        result=AttemptResult.PAUSED,
        failure_code=None,
        now=now or datetime.now().astimezone(),
    )


def _orphan_guard(cutoff: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        WorkflowExecution.status == "running",
        WorkflowExecution.lease_expires_at.is_(None),
        WorkflowExecution.running_since < cutoff,
    )


async def list_orphaned_running(
    session: AsyncSession, *, cutoff: datetime
) -> list[ExecutionRecord]:
    """Running rows with no lease that started before ``cutoff``."""
    rows = await session.scalars(
        select(WorkflowExecution)
        .where(*_orphan_guard(cutoff))
        .execution_options(populate_existing=True)
    )
    return [_record(r) for r in rows]


async def fail_orphaned_if_unchanged(
    session: AsyncSession, execution_id: str, *, cutoff: datetime, error: str, now: datetime
) -> bool:
    """G10: fail a still-orphaned row; a claim or renewal since the scan wins."""
    return await _guarded(
        session,
        execution_id,
        _orphan_guard(cutoff),
        {
            "status": "failed",
            "failure_code": "orphaned_running",
            "error": error,
            "completed_at": now,
            **_NO_LEASE,
        },
    )


async def list_waiting_approval(session: AsyncSession) -> list[ExecutionRecord]:
    rows = await session.scalars(
        select(WorkflowExecution)
        .where(WorkflowExecution.status == "waiting_approval")
        .execution_options(populate_existing=True)
    )
    return [_record(r) for r in rows]


async def fail_expired_approval_if_unchanged(
    session: AsyncSession,
    execution_id: str,
    *,
    variables_snapshot: Any,
    error: str,
    now: datetime,
) -> bool:
    """G11: fail a pause whose ``variables`` still equal the scanned snapshot (JSONB equality)."""
    return await _guarded(
        session,
        execution_id,
        (
            WorkflowExecution.status == "waiting_approval",
            WorkflowExecution.variables == literal(variables_snapshot, JSONB),
        ),
        {"status": "failed", "error": error, "completed_at": now},
    )


async def list_expired_leases(session: AsyncSession, *, now: datetime) -> list[ExecutionRecord]:
    rows = await session.scalars(
        select(WorkflowExecution)
        .where(WorkflowExecution.status == "running", WorkflowExecution.lease_expires_at < now)
        .execution_options(populate_existing=True)
    )
    return [_record(r) for r in rows]


async def fail_expired_lease_if_unchanged(
    session: AsyncSession,
    execution_id: str,
    *,
    lease_expires_at: datetime,
    error: str,
    now: datetime,
) -> bool:
    """G12: fail a running row whose lease expiry is still the scanned value."""
    return await _guarded(
        session,
        execution_id,
        (
            WorkflowExecution.status == "running",
            WorkflowExecution.lease_expires_at == lease_expires_at,
        ),
        {
            "status": "failed",
            "failure_code": "lease_expired",
            "error": error,
            "completed_at": now,
            **_NO_LEASE,
        },
    )


async def claim_orphans_by_email(session: AsyncSession, email: str, user_id: str) -> int:
    """Give ownerless executions migrated for ``email`` to ``user_id``; returns how many."""
    result = await session.execute(
        sa_update(WorkflowExecution)
        .where(WorkflowExecution.original_owner_email == email, WorkflowExecution.user_id.is_(None))
        .values(user_id=user_id)
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())


async def insert_if_absent(session: AsyncSession, values: ExecutionInsert) -> bool:
    """Insert unless a row with this id exists (migration writer). True when inserted.

    Absent ``input``/``output`` stay SQL NULL, as migrated rows always were.
    """
    inserted = await session.scalar(
        pg_insert(WorkflowExecution)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(WorkflowExecution.id)
    )
    return inserted is not None


@dataclass(frozen=True)
class PendingInstance:
    """The approval pause a token was issued for (``_pending_approval_node``/``_since``)."""

    node_id: str
    since: str


async def resume_if_waiting(
    session: AsyncSession,
    execution_id: str,
    *,
    decision: str,
    now: datetime,
    expected_pending: PendingInstance | None = None,
) -> bool:
    """G3/G4: waiting_approval -> running, stamping ``_resume_decision`` in the same statement.

    With ``expected_pending`` the row must still be paused at that exact pause instance.
    """
    guard: list[ColumnElement[bool]] = [
        WorkflowExecution.id == execution_id,
        WorkflowExecution.status == "waiting_approval",
    ]
    if expected_pending is not None:
        guard += [
            WorkflowExecution.variables["_pending_approval_node"].astext
            == expected_pending.node_id,
            WorkflowExecution.variables["_pending_approval_since"].astext == expected_pending.since,
        ]
    won = await session.scalar(
        sa_update(WorkflowExecution)
        .where(*guard)
        .values(
            status="running",
            running_since=now,
            lease_owner=None,
            lease_expires_at=None,
            # jsonb || jsonb merges the key into the CURRENT row value; the right side must be
            # a typed jsonb bind (an untyped str inside jsonb_build_object fails in Postgres).
            variables=type_coerce(
                WorkflowExecution.variables.op("||")(
                    cast(literal({"_resume_decision": decision}, JSONB), JSONB)
                ),
                JSONB,
            ),
        )
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    return won is not None
