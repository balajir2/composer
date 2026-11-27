"""Role-projected, read-only execution history endpoints."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.db.repos import execution_attempts as execution_attempts_repo
from src.db.repos import executions as executions_repo
from src.db.repos.execution_attempts import AttemptRecord
from src.db.repos.executions import ExecutionRecord
from src.db.runtime import require_sessionmaker
from src.security.auth import get_current_role

router = APIRouter(tags=["execution observability"])

_GUIDANCE = {
    "execution_timeout": "The execution timed out. Try again later or contact an administrator.",
    "execution_error": "The execution failed. Contact an administrator if the problem continues.",
    "lease_expired": "Processing stopped after its lease expired. Contact an administrator if the problem continues.",
    "orphaned_running": "Processing stopped unexpectedly. Contact an administrator if the problem continues.",
    "checkpoint_missing": "Execution state is unavailable. Contact an administrator.",
    "checkpoint_sync_failed": "Execution state could not be saved. Contact an administrator.",
    "checkpoint_incompatible": "The workflow was changed while this run was paused, so it cannot continue. Start a new run.",
}
_GENERIC_GUIDANCE = "This execution failed. Contact an administrator if the problem continues."


class RecoveryAssessmentState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    INELIGIBLE_CANCELED = "ineligible_canceled"
    ASSESSMENT_REQUIRED = "assessment_required"


class AdminExecutionDiagnostics(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    thread_id: str = Field(alias="threadId")
    failure_code: str | None = Field(default=None, alias="failureCode")
    delivery_attempts: int = Field(alias="deliveryAttempts")
    lease_owner: str | None = Field(default=None, alias="leaseOwner")
    lease_expires_at: datetime | None = Field(default=None, alias="leaseExpiresAt")


class ExecutionObservabilityRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    execution_id: str = Field(alias="executionId")
    workflow_id: str = Field(alias="workflowId")
    status: str
    current_node_id: str | None = Field(alias="currentNodeId")
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime | None = Field(alias="completedAt")
    history_available: bool = Field(alias="historyAvailable")
    attempt_count: int = Field(alias="attemptCount")
    failure_guidance: str | None = Field(alias="failureGuidance")
    recovery_state: RecoveryAssessmentState = Field(alias="recoveryState")
    observed_at: datetime = Field(alias="observedAt")
    admin_diagnostics: AdminExecutionDiagnostics | None = Field(
        default=None, alias="adminDiagnostics"
    )


class ExecutionAttemptRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    ordinal: int
    started_at: datetime = Field(alias="startedAt")
    ended_at: datetime | None = Field(alias="endedAt")
    result: str
    trigger_kind: str = Field(alias="triggerKind")
    failure_guidance: str | None = Field(alias="failureGuidance")
    has_approval: bool = Field(alias="hasApproval")
    lease_owner: str | None = Field(default=None, alias="leaseOwner")
    lease_expires_at: datetime | None = Field(default=None, alias="leaseExpiresAt")
    failure_code: str | None = Field(default=None, alias="failureCode")
    recovery_source_attempt_id: str | None = Field(default=None, alias="recoverySourceAttemptId")
    approval_id: str | None = Field(default=None, alias="approvalId")
    actor_user_id: str | None = Field(default=None, alias="actorUserId")
    reason_ref: str | None = Field(default=None, alias="reasonRef")


class ExecutionAttemptPage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    history_available: bool = Field(alias="historyAvailable")
    total: int
    limit: int
    offset: int
    items: list[ExecutionAttemptRead]


def _failure_guidance(code: str | None) -> str | None:
    return _GENERIC_GUIDANCE if code is None else _GUIDANCE.get(code, _GENERIC_GUIDANCE)


def _recovery_state(execution_status: str) -> RecoveryAssessmentState:
    if execution_status == "canceled":
        return RecoveryAssessmentState.INELIGIBLE_CANCELED
    if execution_status == "failed":
        return RecoveryAssessmentState.ASSESSMENT_REQUIRED
    return RecoveryAssessmentState.NOT_APPLICABLE


def _base_observability(record: ExecutionRecord, count: int, history: bool) -> dict[str, Any]:
    return {
        "executionId": record.id,
        "workflowId": record.workflow_id,
        "status": record.status,
        "currentNodeId": record.current_node_id,
        "startedAt": record.started_at,
        "completedAt": record.completed_at,
        "historyAvailable": history,
        "attemptCount": count,
        "failureGuidance": (
            _failure_guidance(record.failure_code) if record.status == "failed" else None
        ),
        "recoveryState": _recovery_state(record.status),
        "observedAt": datetime.now(UTC),
    }


def _to_owner_observability(
    record: ExecutionRecord, count: int, history: bool
) -> ExecutionObservabilityRead:
    return ExecutionObservabilityRead(**_base_observability(record, count, history))


def _to_admin_observability(
    record: ExecutionRecord, count: int, history: bool
) -> ExecutionObservabilityRead:
    data = _base_observability(record, count, history)
    data["adminDiagnostics"] = AdminExecutionDiagnostics(
        threadId=record.thread_id,
        failureCode=record.failure_code,
        deliveryAttempts=record.delivery_attempts,
        leaseOwner=record.lease_owner,
        leaseExpiresAt=record.lease_expires_at,
    )
    return ExecutionObservabilityRead(**data)


def _attempt(record: AttemptRecord, *, admin: bool) -> ExecutionAttemptRead:
    values: dict[str, Any] = {
        "id": record.id,
        "ordinal": record.ordinal,
        "startedAt": record.started_at,
        "endedAt": record.ended_at,
        "result": record.result.value,
        "triggerKind": record.trigger_kind.value,
        "failureGuidance": (
            _failure_guidance(record.failure_code) if record.result.value == "failed" else None
        ),
        "hasApproval": record.approval_id is not None,
    }
    if admin:
        values.update(
            {
                "leaseOwner": record.lease_owner,
                "leaseExpiresAt": record.lease_expires_at,
                "failureCode": record.failure_code,
                "recoverySourceAttemptId": record.recovery_source_attempt_id,
                "approvalId": record.approval_id,
                "actorUserId": record.actor_user_id,
                "reasonRef": record.reason_ref,
            }
        )
    return ExecutionAttemptRead(**values)


@router.get(
    "/executions/{execution_id}/observability",
    response_model=ExecutionObservabilityRead,
    response_model_exclude_unset=True,
)
async def get_observability(
    execution_id: str, request: Request, user_role: tuple[str, str] = Depends(get_current_role)
) -> ExecutionObservabilityRead:
    user_id, role = user_role
    async with require_sessionmaker(request)() as session:
        execution = await executions_repo.get(session, execution_id)
        if execution is None or (role != "admin" and execution.user_id != user_id):
            raise HTTPException(status_code=404, detail=f"Execution {execution_id!r} not found.")
        history = await execution_attempts_repo.has_history(session, execution_id)
        _, count = await execution_attempts_repo.list_page(
            session, execution_id=execution_id, limit=1, offset=0
        )
        mapper = _to_admin_observability if role == "admin" else _to_owner_observability
        return mapper(execution, count, history)


@router.get(
    "/executions/{execution_id}/attempts",
    response_model=ExecutionAttemptPage,
    response_model_exclude_unset=True,
)
async def get_attempts(
    execution_id: str,
    request: Request,
    user_role: tuple[str, str] = Depends(get_current_role),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ExecutionAttemptPage:
    user_id, role = user_role
    async with require_sessionmaker(request)() as session:
        execution = await executions_repo.get(session, execution_id)
        if execution is None or (role != "admin" and execution.user_id != user_id):
            raise HTTPException(status_code=404, detail=f"Execution {execution_id!r} not found.")
        rows, total = await execution_attempts_repo.list_page(
            session, execution_id=execution_id, limit=limit, offset=offset
        )
        return ExecutionAttemptPage(
            historyAvailable=total > 0,
            total=total,
            limit=limit,
            offset=offset,
            items=[_attempt(row, admin=role == "admin") for row in rows],
        )


__all__ = [
    "AdminExecutionDiagnostics",
    "ExecutionAttemptPage",
    "ExecutionAttemptRead",
    "ExecutionObservabilityRead",
    "RecoveryAssessmentState",
    "router",
]
