"""POST /executions (start a run) + GET /executions/{id} (fetch state)."""

import dataclasses
import json as _json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.api._idempotency import find_execution_by_idempotency_key, require_same_caller
from src.api.execution_status import ACTIVE_EXECUTION_STATUSES
from src.config import get_settings
from src.db.errors import AlreadyExists
from src.db.models import ApprovalDecision
from src.db.repos import approvals as approvals_repo
from src.db.repos import executions as executions_repo
from src.db.repos import workflow_assignments as workflow_assignments_repo
from src.db.repos import workflows as workflows_repo
from src.db.repos.executions import ExecutionRecord
from src.db.runtime import require_sessionmaker
from src.db.types import utcnow
from src.engine.langgraph_executor import LangGraphExecutor
from src.execution.cloud_tasks import enqueue_execution
from src.security.auth import get_current_role
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["executions"])

# Deletion/cancellation endpoints reject/skip rows in these statuses (P1-6,
# extended P1-2-followup) — deleting or racing a still-active execution's
# LangGraph checkpoints out from under it leaves whichever worker eventually
# claims and drives it (via POST /internal/claim-and-run, Cloud-Tasks-push)
# unable to persist a final state against a row (and checkpoints) that no
# longer exist. `queued` MUST be included here even though the row hasn't
# been claimed yet: a `queued` row can be actively in flight to Cloud Tasks,
# or already claimed by a concurrent claim-and-run delivery by the time a
# delete request's unlocked read observes it — there is no persisted status
# between row-creation and terminal completion that is ever safe to delete
# unconditionally.
#
# Shared cancellation/deletion vocabulary comes from `src.api.execution_status`.
# Worker claims use narrower, delivery-kind-specific eligibility.


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(alias="workflowId")
    input: Any = None
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class ExecutionRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    workflow_id: str = Field(alias="workflowId")
    user_id: str | None = Field(default=None, alias="userId")
    status: str
    current_node_id: str | None = Field(default=None, alias="currentNodeId")
    node_results: dict[str, Any] = Field(default_factory=dict, alias="nodeResults")
    variables: dict[str, Any] = Field(default_factory=dict)
    input: Any = None
    output: Any = None
    error: str | None = None
    failure_code: str | None = Field(default=None, alias="failureCode")
    started_at: Any = Field(alias="startedAt")
    completed_at: Any = Field(default=None, alias="completedAt")
    thread_id: str = Field(alias="threadId")


class ExecutionListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    items: list[ExecutionRead]
    limit: int
    offset: int


class ResumeDecision(StrEnum):
    approved = "approved"
    rejected = "rejected"


class ResumeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: ResumeDecision
    note: str | None = None


def _to_execution_read(record: ExecutionRecord) -> ExecutionRead:
    """Build the API model from a repository record (snake_case attributes)."""
    return ExecutionRead(
        id=record.id,
        workflowId=record.workflow_id,
        userId=record.user_id,
        status=record.status,
        currentNodeId=record.current_node_id,
        nodeResults=record.node_results or {},
        variables=record.variables or {},
        input=record.input,
        output=record.output,
        error=record.error,
        failureCode=record.failure_code,
        startedAt=record.started_at,
        completedAt=record.completed_at,
        threadId=record.thread_id,
    )


def _get_executor(request: Request) -> LangGraphExecutor:
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    event_bus = getattr(request.app.state, "event_bus", None)
    return LangGraphExecutor(
        sessionmaker=require_sessionmaker(request),
        checkpointer=checkpointer,
        event_bus=event_bus,
    )


@router.post("/executions", response_model=ExecutionRead, status_code=status.HTTP_202_ACCEPTED)
async def create_execution(
    payload: ExecutionCreate,
    request: Request,
    _role: tuple[str, str] = Depends(get_current_role),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> ExecutionRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    await enforce(
        limiter,
        route_key="executions",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_executions_per_minute),
    )
    # P1-6: measure true UTF-8 byte size, not the character length of
    # json.dumps()'s default ensure_ascii=True output — that default
    # escapes every non-ASCII character to a `\uXXXX` sequence (6 chars
    # for a 3-byte UTF-8 character), which over-counts non-ASCII payloads
    # and can reject inputs well under the real byte cap.
    input_size = len(_json.dumps(payload.input, default=str, ensure_ascii=False).encode("utf-8"))
    max_bytes = get_settings().max_execution_input_bytes
    if input_size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"execution input exceeds max_bytes={max_bytes}; got {input_size}",
        )

    # Short session: closed before the execution row is written and the task enqueued.
    # Access: admin, public, owner, or an assignee (the assignment is only read when the
    # cheaper checks fail). Anyone else gets the same 404 as a missing workflow.
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Workflow {payload.workflow_id!r} not found.",
    )
    # Idempotent replay (P1-3): a caller that retries this POST after a network timeout is
    # the actual duplicate-execution vector in this system — LangGraph doesn't auto-retry
    # nodes, and the stuck-execution sweeper only marks abandoned runs failed, it never
    # re-runs them. A matching (workflowId, userId, idempotencyKey) row means an earlier
    # request already started (or finished) this logical operation; return it instead of
    # starting a second one that would fire side-effecting nodes a second time. The lookup
    # shares the workflow read's short session.
    sessionmaker = require_sessionmaker(request)
    replay: ExecutionRecord | None = None
    async with sessionmaker() as session:
        workflow = await workflows_repo.get(session, payload.workflow_id)
        if workflow is None:
            raise not_found
        allowed = role == "admin" or workflow.is_public or workflow.user_id == user_id
        if not allowed:
            allowed = await workflow_assignments_repo.exists(session, payload.workflow_id, user_id)
        if allowed and payload.idempotency_key:
            replay = await find_execution_by_idempotency_key(
                session, payload.workflow_id, user_id, payload.idempotency_key
            )
    if not allowed:
        raise not_found
    if replay is not None:
        require_same_caller(replay, user_id)
        return _to_execution_read(replay)

    executor = _get_executor(request)
    try:
        row = await executor.start_execution(
            workflow_id=payload.workflow_id,
            input=payload.input,
            user_id=user_id,
            idempotency_key=payload.idempotency_key,
        )
    except AlreadyExists:
        # Lost a race against a concurrent request carrying the same key —
        # the DB's unique constraint is the actual guarantee; the find_first
        # lookup above is just a fast path that can't close the race alone.
        if payload.idempotency_key is None:
            raise
        async with sessionmaker() as session:
            existing = await find_execution_by_idempotency_key(
                session, payload.workflow_id, user_id, payload.idempotency_key
            )
        if existing is None:
            # Only reachable while the DB still has the old per-workflow unique
            # index (code deployed before alembic 0002): the key is another
            # user's. Say so without leaking whose.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="idempotency key already used for this workflow",
            ) from None
        require_same_caller(existing, user_id)
        return _to_execution_read(existing)

    # P1-2: enqueue a Cloud Task instead of a request-bound BackgroundTask —
    # Cloud Run can scale a request-bound background task's instance to
    # zero mid-run (confirmed via --min-instances=0 in the deploy config,
    # ADR-0033). Cloud Tasks' HTTP-push delivery to /internal/claim-and-run
    # is a real inbound request, which Cloud Run won't recycle mid-flight.
    # The response returns with status='queued' immediately; the row only
    # becomes 'running' once claim-and-run actually claims it. Poll
    # GET /executions/{id} for completion.
    try:
        await enqueue_execution(row.id, kind="run")
    except Exception as exc:
        # The row already committed as 'queued' above. If enqueueing raises
        # here (un-retried gRPC error, transient network blip, IAM/ADC
        # misconfiguration), the row would otherwise be stuck at 'queued'
        # forever — invisible to both sweepers (sweep_orphaned_running and
        # sweep_expired_leases both only scan status='running') and, if the
        # caller supplied an idempotencyKey and retries per the documented
        # pattern above, the idempotency short-circuit would keep returning
        # this same stuck row without ever calling enqueue_execution again.
        # Mark it failed immediately instead — mirrors
        # LangGraphExecutor.run()'s own except-and-mark-failed handling —
        # so the row reaches a terminal status right away and the caller
        # gets a real, actionable error instead of an unhandled 500.
        logger.exception("create_execution: failed to enqueue Cloud Task for execution %s", row.id)
        try:
            # Guarded like every other status transition in this file
            # (resume_execution, cancel_execution): fold the CURRENT status
            # into the WHERE clause via update_many instead of an
            # unconditional update(). 'queued' is now a cancellable status
            # (P1-2-followup) — if a client cancels this still-'queued' row
            # while enqueue_execution is mid-flight/timing out,
            # cancel_execution's own atomic update_many could win first
            # (row -> 'canceled'). Guarding on status='queued' here makes
            # this write a safe no-op in that case instead of clobbering a
            # legitimate cancellation with a stale 'failed'.
            async with sessionmaker() as session:
                await executions_repo.mark_enqueue_failed_if_queued(
                    session,
                    row.id,
                    error=f"failed to enqueue execution for durable processing: {exc}",
                    now=datetime.now(UTC),
                )
                await session.commit()
        except Exception:
            # Nested-failure path: if THIS write also raises (a DB blip
            # right after row's own successful create()), we must not let
            # it propagate — that would replace the informative
            # HTTPException below with FastAPI's generic 500 handler AND
            # leave the row stuck at 'queued' forever, reproducing the
            # exact failure mode this whole handler exists to prevent, via
            # a different path. Log distinctly so operators can find this
            # rare double-failure via the sweeper's stuck-execution scan or
            # manual intervention, then fall through to raise the primary,
            # more informative error below — don't let a secondary DB
            # error mask the original problem, and don't silently swallow
            # it either.
            logger.exception(
                "create_execution: failed to mark execution %s as failed after enqueue error",
                row.id,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution {row.id!r} could not be enqueued for processing.",
        ) from exc
    return _to_execution_read(row)


@router.get("/executions", response_model=ExecutionListResponse)
async def list_executions(
    request: Request,
    _role: tuple[str, str] = Depends(get_current_role),
    workflow_id: str | None = Query(default=None, alias="workflowId"),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ExecutionListResponse:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    async with require_sessionmaker(request)() as session:
        records, total = await executions_repo.list_page(
            session,
            owner_id=None if role == "admin" else user_id,
            workflow_id=workflow_id,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
    items = [_to_execution_read(r) for r in records]
    return ExecutionListResponse(total=total, items=items, limit=limit, offset=offset)


@router.get("/executions/{execution_id}", response_model=ExecutionRead)
async def get_execution(
    execution_id: str,
    request: Request,
    _role: tuple[str, str] = Depends(get_current_role),
) -> ExecutionRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    async with require_sessionmaker(request)() as session:
        row = await executions_repo.get(session, execution_id)
    if row is None or (role != "admin" and row.user_id != user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    return _to_execution_read(row)


class BulkDeleteRequest(BaseModel):
    """Body for `POST /executions/delete-bulk`.

    Two modes — both filter to the caller's authz scope (members see
    only their own executions; admins see all):

    * `executionIds` — explicit list of ids (preferred — what the
      history-page checkboxes produce).
    * `allInScope: true` — wipe every execution the caller can see.
      Admin-only safety valve for "clear all history on this
      deployment"; rejected (403) for non-admins to keep an
      accidental click from emptying a member's full history.
    """

    model_config = ConfigDict(populate_by_name=True)

    execution_ids: list[str] | None = Field(default=None, alias="executionIds")
    all_in_scope: bool = Field(default=False, alias="allInScope")


class BulkDeleteResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    deleted_count: int = Field(alias="deletedCount")
    skipped_count: int = Field(default=0, alias="skippedCount")


@router.post(
    "/executions/delete-bulk",
    response_model=BulkDeleteResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_executions_bulk(  # pyright: ignore[reportUnusedFunction]
    payload: BulkDeleteRequest,
    request: Request,
    _role: tuple[str, str] = Depends(get_current_role),
) -> BulkDeleteResponse:
    """Delete multiple executions in one round-trip.

    Same authz model as the single-row delete: members can only
    delete their own; admins can delete any.  We resolve the target
    set under that scope BEFORE deletion so the count returned is
    honest (skipped = ids the caller asked for but didn't own).

    `allInScope=true` is admin-only — see BulkDeleteRequest docstring.
    """
    user_id, role = _role

    if payload.all_in_scope and role != "admin":
        # Members can still call delete-bulk with their own id list,
        # but the "wipe everything" mode is admin-only.  Members
        # accidentally clicking "Delete all" elsewhere shouldn't
        # nuke their own history.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="allInScope=true is admin-only.",
        )

    requested_ids = payload.execution_ids or []
    if not payload.all_in_scope and not requested_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provide executionIds or set allInScope=true.",
        )

    # Resolve the target set under the caller's scope and delete it in ONE transaction
    # (checkpoints and events of exactly the rows deleted). allInScope takes every row
    # (admin only); explicit ids are intersected with the caller's own for members so
    # admin-only ids can't slip through. Active executions are skipped (P1-6): deleting a
    # running or waiting_approval row out from under its in-flight task leaves that task
    # unable to persist a final state against a row that's gone.
    async with require_sessionmaker(request)() as session:
        refs = await executions_repo.list_refs(
            session,
            ids=None if payload.all_in_scope else requested_ids,
            owner_id=None if role == "admin" else user_id,
        )
        target_ids = [r.id for r in refs if r.status not in ACTIVE_EXECUTION_STATUSES]
        deleted = await executions_repo.delete_terminal(session, target_ids)
        await session.commit()

    skipped = max(0, len(requested_ids) - len(deleted)) if requested_ids else 0
    return BulkDeleteResponse(deletedCount=len(deleted), skippedCount=skipped)


@router.delete("/executions/{execution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_execution(  # pyright: ignore[reportUnusedFunction]
    execution_id: str,
    request: Request,
    _role: tuple[str, str] = Depends(get_current_role),
) -> None:
    """Delete an execution + its derived artefacts.

    Authz: owner OR admin.  Admins can clean up any user's history;
    members can only delete their own (matches the read-authz policy
    everywhere else).  404 (not 403) on cross-tenant access so we
    don't leak existence.

    Cascade scope:
      - `approvals` rows — via Prisma `onDelete: Cascade` on the FK.
      - LangGraph checkpoints + checkpoint_writes — keyed by
        `thread_id` (no FK to execution by design — checkpoints can
        outlive the execution row in some scenarios).  We delete
        them explicitly here because once the execution is gone the
        checkpoints are unreachable garbage.
      - `execution_events` — no FK to the execution either (same
        rationale as checkpoints, P1-4).  Deleted explicitly so
        events don't orphan permanently.
      - `workflow_executions` row itself.
    """
    user_id, role = _role
    async with require_sessionmaker(request)() as session:
        row = await executions_repo.get(session, execution_id)
        # Same 404 for a missing row and a non-owner: don't leak existence.
        if row is None or (role != "admin" and row.user_id != user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id!r} not found.",
            )
        if row.status in ACTIVE_EXECUTION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Execution {execution_id!r} is {row.status!r} — cancel it first, or wait "
                    "for it to reach a terminal state, before deleting."
                ),
            )
        # Checkpoints, events and the row go in one transaction.
        await executions_repo.delete_terminal(session, [execution_id])
        await session.commit()


_CANCEL_ERROR_MESSAGE = "Execution canceled by user request."


@router.post("/executions/{execution_id}/cancel", response_model=ExecutionRead)
async def cancel_execution(  # pyright: ignore[reportUnusedFunction]
    execution_id: str,
    request: Request,
    _role: tuple[str, str] = Depends(get_current_role),
) -> ExecutionRead:
    """First-class cancellation (P1-6): the public status vocabulary
    documents `canceled`, but nothing ever wrote it — worker shutdown
    persisted `failed`, and there was no user-triggered cancel operation.

    Execution stops cooperatively at graph boundaries. A side-effecting
    node already in flight may still complete; cancellation releases the
    attempt's ownership without deleting its checkpoints or pending writes.
    """
    user_id, role = _role
    async with require_sessionmaker(request)() as session:
        execution = await executions_repo.get(session, execution_id)
        if execution is None or (role != "admin" and execution.user_id != user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id!r} not found.",
            )
        if execution.status not in ACTIVE_EXECUTION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Execution is {execution.status!r}, not cancellable.",
            )
        # Atomic conditional transition (G2): the status guard is part of the UPDATE, so
        # two concurrent cancels (or a cancel racing a resume or claim-and-run's claim)
        # can't both succeed. Canceling a still-queued row is safe: claim-and-run's claim no
        # longer matches once the row is 'canceled', so the eventual Cloud Task delivery
        # finds nothing to claim and returns 'already_claimed'.
        canceled = await executions_repo.cancel_if_active(
            session, execution_id, error=_CANCEL_ERROR_MESSAGE, now=datetime.now(UTC)
        )
        await session.commit()
    if not canceled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution {execution_id!r} is no longer cancellable "
            "(already resolved by a concurrent request).",
        )

    # The response carries the pre-update row with only status and error replaced.
    return _to_execution_read(
        dataclasses.replace(execution, status="canceled", error=_CANCEL_ERROR_MESSAGE)
    )


@router.post("/executions/{execution_id}/resume", response_model=ExecutionRead)
async def resume_execution(
    execution_id: str,
    payload: ResumeRequest,
    request: Request,
    _role: tuple[str, str] = Depends(get_current_role),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> ExecutionRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    await enforce(
        limiter,
        route_key="resume",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_resume_per_minute),
    )
    sessionmaker = require_sessionmaker(request)
    async with sessionmaker() as session:
        execution = await executions_repo.get(session, execution_id)
        if execution is None or (role != "admin" and execution.user_id != user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id!r} not found.",
            )
        if execution.status != "waiting_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Execution is {execution.status!r}, not 'waiting_approval'.",
            )
        variables = execution.variables
        pending_node_id: Any = (
            cast("dict[str, Any]", variables).get("_pending_approval_node")
            if isinstance(variables, dict)
            else None
        )
        if pending_node_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Execution is waiting_approval but has no _pending_approval_node.",
            )

        # Atomic conditional transition (P1-1): the earlier status check gives a friendly
        # error for the common case, but two near-simultaneous resumes (a double-click, two
        # reviewers racing) could both pass it. One guarded UPDATE folds the guard into the
        # statement that flips the row, so at most one request wins. It also starts the new
        # attempt's claim grace period, clears any legacy lease, and (P1-2, Task 13) stamps
        # the decision into `variables`: enqueue_execution's Cloud Task body carries no room
        # for it, so it travels as row state that claim-and-run reads back after claiming.
        # The approval audit row commits in the same transaction (phase 4b, DB1).
        won = await executions_repo.resume_if_waiting(
            session, execution_id, decision=payload.decision.value, now=utcnow()
        )
        if not won:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Execution {execution_id!r} is no longer waiting_approval "
                "(already resolved by a concurrent request).",
            )
        await approvals_repo.create(
            session,
            execution_id=execution_id,
            node_id=str(pending_node_id),
            decision=ApprovalDecision(payload.decision.value),
            approver_user_id=user_id,
            approver_email=None,
            via_email_link=False,
            note=payload.note,
        )
        await session.commit()
    # The session is closed: enqueue is network I/O (short-session rule).
    await enqueue_execution(execution_id, kind="resume")

    # Reflect the transition without a second round trip (DB5: pre-update row, status replaced).
    return _to_execution_read(dataclasses.replace(execution, status="running"))


__all__ = [
    "ExecutionCreate",
    "ExecutionListResponse",
    "ExecutionRead",
    "ResumeDecision",
    "ResumeRequest",
    "router",
]
