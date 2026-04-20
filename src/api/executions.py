"""POST /executions (start a run) + GET /executions/{id} (fetch state)."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.engine.langgraph_executor import LangGraphExecutor
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


def _get_executor(request: Request, db: Prisma) -> LangGraphExecutor:  # pyright: ignore[reportUnknownParameterType]
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    return LangGraphExecutor(db=db, checkpointer=checkpointer)


@router.post("/executions", response_model=ExecutionRead, status_code=status.HTTP_202_ACCEPTED)
async def create_execution(
    payload: ExecutionCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> ExecutionRead:  # pyright: ignore[reportUnusedFunction]
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
        workflow_id=payload.workflow_id, input=payload.input, user_id="dev"
    )
    # Schedule the actual run in the background. The response returns with
    # status='running' immediately; poll GET /executions/{id} for completion.
    background_tasks.add_task(executor.run, row.id)
    return ExecutionRead.model_validate(row)


@router.get("/executions/{execution_id}", response_model=ExecutionRead)
async def get_execution(
    execution_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> ExecutionRead:  # pyright: ignore[reportUnusedFunction]
    row = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": execution_id}
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id!r} not found.",
        )
    return ExecutionRead.model_validate(row)


__all__ = ["ExecutionCreate", "ExecutionRead", "router"]
