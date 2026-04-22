"""POST /executions (start a run) + GET /executions/{id} (fetch state)."""

import json as _json
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.engine.events import ExecutionEvent, ExecutionEventBus
from src.engine.langgraph_executor import LangGraphExecutor
from src.security.auth import get_current_role, get_current_user_id
from src.security.rate_limit import (
    RateLimiter,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db

router = APIRouter(tags=["executions"])


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(alias="workflowId")
    input: Any = None


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


@router.post("/executions", response_model=ExecutionRead, status_code=status.HTTP_202_ACCEPTED)
async def create_execution(
    payload: ExecutionCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> ExecutionRead:  # pyright: ignore[reportUnusedFunction]
    await enforce(
        limiter,
        route_key="executions",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_executions_per_minute),
    )
    input_size = len(_json.dumps(payload.input, default=str))
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

    executor = _get_executor(request, db)
    row = await executor.start_execution(
        workflow_id=payload.workflow_id, input=payload.input, user_id=user_id
    )
    # Schedule the actual run in the background. The response returns with
    # status='running' immediately; poll GET /executions/{id} for completion.
    background_tasks.add_task(executor.run, row.id)
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

    total = await db.workflowexecution.count(where=where)  # pyright: ignore[reportAttributeAccessIssue]
    rows = await db.workflowexecution.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,
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


@router.post("/executions/{execution_id}/resume", response_model=ExecutionRead)
async def resume_execution(
    execution_id: str,
    payload: ResumeRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
    limiter: RateLimiter = Depends(get_rate_limiter),
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

    # Audit trail
    await db.approval.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "executionId": execution_id,
            "nodeId": pending_node_id,
            "approverUserId": user_id,
            "decision": payload.decision.value,
            "note": payload.note,
        }
    )

    # Flip status to 'running' BEFORE scheduling the BackgroundTask, so polling
    # sees consistent state while the background work runs.
    updated = await db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id},
        data={"status": "running"},
    )

    event_bus: ExecutionEventBus | None = getattr(request.app.state, "event_bus", None)
    if event_bus is not None:
        await event_bus.emit(
            ExecutionEvent(
                type="approval-resumed",
                execution_id=execution_id,
                payload={"node_id": pending_node_id, "decision": payload.decision.value},
            )
        )

    executor = _get_executor(request, db)
    background_tasks.add_task(executor.resume, execution_id, payload.decision.value)

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found after update.",
        )
    return ExecutionRead.model_validate(updated)


__all__ = [
    "ExecutionCreate",
    "ExecutionListResponse",
    "ExecutionRead",
    "ResumeDecision",
    "ResumeRequest",
    "router",
]
