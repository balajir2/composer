"""POST /executions (start a run) + GET /executions/{id} (fetch state)."""

import json as _json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.engine.langgraph_executor import LangGraphExecutor
from src.execution.cloud_tasks import enqueue_execution
from src.security.auth import get_current_role
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db

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
_ACTIVE_EXECUTION_STATUSES = frozenset({"queued", "running", "waiting_approval"})

# Sorted, deterministic ordering for use in `{"in": [...]}` where-clauses
# (dict/list equality in tests + stable SQL param ordering) — derived from
# the frozenset above so the two can never drift apart.
_ACTIVE_EXECUTION_STATUSES_SORTED = sorted(_ACTIVE_EXECUTION_STATUSES)


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(alias="workflowId")
    input: Any = None
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class ExecutionRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

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


def _get_executor(request: Request, db: Prisma) -> LangGraphExecutor:  # pyright: ignore[reportUnknownParameterType]
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    event_bus = getattr(request.app.state, "event_bus", None)
    return LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)


async def _find_execution_by_idempotency_key(
    db: Prisma,  # pyright: ignore[reportUnknownParameterType]
    workflow_id: str,
    idempotency_key: str,
) -> Any:
    where: dict[str, Any] = {
        "workflowId_idempotencyKey": {
            "workflowId": workflow_id,
            "idempotencyKey": idempotency_key,
        }
    }
    return await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where=where  # pyright: ignore[reportArgumentType]
    )


@router.post("/executions", response_model=ExecutionRead, status_code=status.HTTP_202_ACCEPTED)
async def create_execution(
    payload: ExecutionCreate,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
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
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"execution input exceeds max_bytes={max_bytes}; got {input_size}",
        )

    workflow = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": payload.workflow_id}
    )
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {payload.workflow_id!r} not found.",
        )
    if role != "admin" and not workflow.isPublic and workflow.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {payload.workflow_id!r} not found.",
        )

    # Idempotent replay (P1-3): a caller that retries this POST after a
    # network timeout is the actual duplicate-execution vector in this
    # system — LangGraph doesn't auto-retry nodes, and the stuck-execution
    # sweeper only marks abandoned runs failed, it never re-runs them.
    # A matching (workflowId, idempotencyKey) row means an earlier request
    # already started (or finished) this logical operation; return it
    # instead of starting a second one that would fire side-effecting
    # nodes (Jira, email, HTTP mutations) a second time.
    if payload.idempotency_key:
        existing = await _find_execution_by_idempotency_key(
            db, payload.workflow_id, payload.idempotency_key
        )
        if existing is not None:
            return ExecutionRead.model_validate(existing)

    executor = _get_executor(request, db)
    try:
        row = await executor.start_execution(
            workflow_id=payload.workflow_id,
            input=payload.input,
            user_id=user_id,
            idempotency_key=payload.idempotency_key,
        )
    except UniqueViolationError:
        # Lost a race against a concurrent request carrying the same key —
        # the DB's unique constraint is the actual guarantee; the find_unique
        # lookup above is just a fast path that can't close the race alone.
        if payload.idempotency_key is None:
            raise
        existing = await _find_execution_by_idempotency_key(
            db, payload.workflow_id, payload.idempotency_key
        )
        if existing is None:
            raise
        return ExecutionRead.model_validate(existing)

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
        # forever — invisible to both sweepers (sweep_stuck_executions and
        # sweep_expired_leases both only scan status='running') and, if the
        # caller supplied an idempotencyKey and retries per the documented
        # pattern above, the idempotency short-circuit would keep returning
        # this same stuck row without ever calling enqueue_execution again.
        # Mark it failed immediately instead — mirrors
        # LangGraphExecutor.run()'s own except-and-mark-failed handling —
        # so the row reaches a terminal status right away and the caller
        # gets a real, actionable error instead of an unhandled 500.
        logger.exception("create_execution: failed to enqueue Cloud Task for execution %s", row.id)
        await db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": row.id},
            data={
                "status": "failed",
                "error": f"failed to enqueue execution for durable processing: {exc}",
                "completedAt": datetime.now(UTC),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution {row.id!r} could not be enqueued for processing.",
        ) from exc
    return ExecutionRead.model_validate(row)


@router.get("/executions", response_model=ExecutionListResponse)
async def list_executions(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
    workflow_id: str | None = Query(default=None, alias="workflowId"),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ExecutionListResponse:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    where: dict[str, Any] = {}
    if role != "admin":
        where["userId"] = user_id
    if workflow_id is not None:
        where["workflowId"] = workflow_id
    if status_filter is not None:
        where["status"] = status_filter

    total = await db.workflowexecution.count(where=where)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    rows = await db.workflowexecution.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,  # pyright: ignore[reportArgumentType]
        take=limit,
        skip=offset,
        order={"startedAt": "desc"},
    )
    items = [ExecutionRead.model_validate(row) for row in rows]
    return ExecutionListResponse(total=total, items=items, limit=limit, offset=offset)


@router.get("/executions/{execution_id}", response_model=ExecutionRead)
async def get_execution(
    execution_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> ExecutionRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    row = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id}
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if role != "admin" and row.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    return ExecutionRead.model_validate(row)


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
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
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

    where: dict[str, Any]
    requested_ids = payload.execution_ids or []
    if payload.all_in_scope:
        # Admin wipe — every row.  No scope narrowing because
        # admins see everything.
        where = {}
    elif requested_ids:
        # Explicit-ids mode.  Intersect with the caller's authz
        # scope so member callers can't pass admin-only ids and
        # have them slip through.
        where = {"id": {"in": requested_ids}}
        if role != "admin":
            where["userId"] = user_id
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide executionIds or set allInScope=true.",
        )

    # Find the matching rows so we can collect thread_ids for the
    # checkpoint cleanup step.  Without this we'd leave orphan
    # checkpoints behind.
    rows = await db.workflowexecution.find_many(where=where)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    # Skip active executions (P1-6): deleting a running or waiting_approval
    # row's checkpoints out from under its in-flight background task leaves
    # that task unable to persist a final state against a row that's gone.
    rows = [row for row in rows if row.status not in _ACTIVE_EXECUTION_STATUSES]
    if not rows:
        return BulkDeleteResponse(
            deletedCount=0,
            skippedCount=len(requested_ids),
        )

    thread_ids = [row.threadId for row in rows]
    target_ids = [row.id for row in rows]

    # Order matters: writes table is FK'd into the checkpoints table,
    # so writes go first, then checkpoints, then the executions
    # themselves (which cascade-delete approvals via Prisma).
    await db.langgraphcheckpointwrite.delete_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"threadId": {"in": thread_ids}}
    )
    await db.langgraphcheckpoint.delete_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"threadId": {"in": thread_ids}}
    )
    await db.executionevent.delete_many(where={"executionId": {"in": target_ids}})  # pyright: ignore[reportAttributeAccessIssue]
    await db.workflowexecution.delete_many(where={"id": {"in": target_ids}})  # pyright: ignore[reportAttributeAccessIssue]

    skipped = max(0, len(requested_ids) - len(target_ids)) if requested_ids else 0
    return BulkDeleteResponse(
        deletedCount=len(target_ids),
        skippedCount=skipped,
    )


@router.delete("/executions/{execution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_execution(  # pyright: ignore[reportUnusedFunction]
    execution_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
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
    row = await db.workflowexecution.find_unique(where={"id": execution_id})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if role != "admin" and row.userId != user_id:
        # Same 404 we use on read for non-owners — don't leak existence.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if row.status in _ACTIVE_EXECUTION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Execution {execution_id!r} is {row.status!r} — cancel it first, or wait "
                "for it to reach a terminal state, before deleting."
            ),
        )

    thread_id = row.threadId
    # Order matters: writes table is FK'd into the checkpoint table,
    # so writes go first, then checkpoints, then the execution itself.
    # Approvals cascade through the execution FK.
    await db.langgraphcheckpointwrite.delete_many(where={"threadId": thread_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.langgraphcheckpoint.delete_many(where={"threadId": thread_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.executionevent.delete_many(where={"executionId": execution_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.workflowexecution.delete(where={"id": execution_id})  # pyright: ignore[reportAttributeAccessIssue]


_CANCEL_ERROR_MESSAGE = "Execution canceled by user request."


@router.post("/executions/{execution_id}/cancel", response_model=ExecutionRead)
async def cancel_execution(  # pyright: ignore[reportUnusedFunction]
    execution_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> ExecutionRead:
    """First-class cancellation (P1-6): the public status vocabulary
    documents `canceled`, but nothing ever wrote it — worker shutdown
    persisted `failed`, and there was no user-triggered cancel operation.

    Known limitation: this marks the row canceled but does not preempt an
    in-flight claim-and-run delivery (P1-2: execution is driven by
    POST /internal/claim-and-run, not a request-bound background task) —
    LangGraph has no cooperative-cancellation hook wired through the
    executor today. Any side-effecting node (Jira, email, HTTP) already
    in flight when cancel is called still
    completes; this stops the row from looking permanently stuck and gives
    callers a real terminal status to key off, which is the concrete gap
    this closes. True mid-node preemption is a separate, larger change.
    """
    user_id, role = _role
    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id}
    )
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if role != "admin" and execution.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if execution.status not in _ACTIVE_EXECUTION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution is {execution.status!r}, not cancellable.",
        )

    # Atomic conditional transition (same pattern as resume_execution, P1-1)
    # — folds the status guard into the update itself so two concurrent
    # cancel calls (or a cancel racing a resume, or a cancel racing
    # claim-and-run's own claim) can't both succeed. Sourced from
    # `_ACTIVE_EXECUTION_STATUSES_SORTED` (not a separately-hardcoded list)
    # so this can never silently drift from the guard check above it —
    # this now includes 'queued', so canceling a not-yet-claimed execution
    # succeeds here instead of hitting the 409 above. Safe to cancel while
    # queued: claim-and-run's own claim query
    # (`status IN ('queued', 'running', 'waiting_approval')`, see
    # src/api/internal.py) no longer matches once this flips the row to
    # 'canceled', so the eventual Cloud Task delivery finds nothing to
    # claim and returns 'already_claimed' — no separate task-cancellation
    # call is needed.
    updated_count = await db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id, "status": {"in": _ACTIVE_EXECUTION_STATUSES_SORTED}},
        data={
            "status": "canceled",
            "error": _CANCEL_ERROR_MESSAGE,
            "completedAt": datetime.now(UTC),
        },
    )
    if updated_count != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution {execution_id!r} is no longer cancellable "
            "(already resolved by a concurrent request).",
        )

    execution.status = "canceled"
    execution.error = _CANCEL_ERROR_MESSAGE
    return ExecutionRead.model_validate(execution)


@router.post("/executions/{execution_id}/resume", response_model=ExecutionRead)
async def resume_execution(
    execution_id: str,
    payload: ResumeRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
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
    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id}
    )
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if role != "admin" and execution.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    if execution.status != "waiting_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution is {execution.status!r}, not 'waiting_approval'.",
        )

    variables = execution.variables or {}
    pending_node_id = (
        variables.get("_pending_approval_node") if isinstance(variables, dict) else None
    )
    if pending_node_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Execution is waiting_approval but has no _pending_approval_node.",
        )

    # Atomic conditional status transition (P1-1) — the earlier status
    # check above (`execution.status != "waiting_approval"`) is a
    # separate find_unique and gives a friendly error for the common
    # case, but two near-simultaneous in-app resume requests (a
    # double-click, two reviewers racing) could both pass that check
    # before either commits. update_many folds the guard into the same
    # statement that performs the transition — at most one request can
    # ever flip this row, matching the same pattern already used for the
    # emailed-link path (src/api/approval_email.py).
    updated_count = await db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id, "status": "waiting_approval"},
        data={"status": "running"},
    )
    if updated_count != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution {execution_id!r} is no longer waiting_approval "
            "(already resolved by a concurrent request).",
        )

    # Audit trail — only recorded once the atomic transition above
    # confirms this request is the single winner.
    approval_data: dict[str, Any] = {
        "executionId": execution_id,
        "nodeId": pending_node_id,
        "approverUserId": user_id,
        "decision": payload.decision.value,
        "note": payload.note,
    }
    await db.approval.create(  # pyright: ignore[reportAttributeAccessIssue]
        data=approval_data,  # pyright: ignore[reportArgumentType]
    )

    executor = _get_executor(request, db)
    background_tasks.add_task(executor.resume, execution_id, payload.decision.value)

    # Reflect the transition we just made atomically without a second
    # DB round-trip — `execution` is the row fetched above, still valid
    # except for the status field update_many just applied.
    execution.status = "running"
    return ExecutionRead.model_validate(execution)


__all__ = [
    "ExecutionCreate",
    "ExecutionListResponse",
    "ExecutionRead",
    "ResumeDecision",
    "ResumeRequest",
    "router",
]
