"""POST /workflows — create a workflow from JSON."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.engine.graph_builder import WorkflowValidationError, validate_workflow_shape
from src.engine.workflow import Workflow, WorkflowEdge, WorkflowNode

router = APIRouter(tags=["workflows"])


def _get_db(request: Request) -> Prisma:  # pyright: ignore[reportUnknownParameterType]
    """FastAPI dependency — returns the app-wide Prisma client from app.state."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise RuntimeError("Prisma client not attached to app.state — did lifespan run?")
    return db  # pyright: ignore[reportReturnType]


class WorkflowCreate(BaseModel):
    """Request body for POST /workflows."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    difficulty: str | None = None
    estimated_time: str | None = Field(default=None, alias="estimatedTime")
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    version: str | None = None
    is_template: bool = Field(default=False, alias="isTemplate")
    is_public: bool = Field(default=False, alias="isPublic")


class WorkflowRead(BaseModel):
    """Response body for /workflows endpoints."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    user_id: str | None = Field(default=None, alias="userId")
    name: str
    description: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    difficulty: str | None = None
    estimated_time: str | None = Field(default=None, alias="estimatedTime")
    nodes: list[Any]
    edges: list[Any]
    version: str | None = None
    is_template: bool = Field(default=False, alias="isTemplate")
    is_public: bool = Field(default=False, alias="isPublic")
    created_at: Any = Field(alias="createdAt")
    updated_at: Any = Field(alias="updatedAt")


@router.post("/workflows", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    db: Prisma = Depends(_get_db),  # pyright: ignore[reportUnknownParameterType]
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    # Re-validate as Workflow to run graph_builder's shape check.
    workflow = Workflow.model_validate(payload.model_dump(by_alias=True))
    try:
        validate_workflow_shape(workflow)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    row = await db.workflow.create(
        data={  # pyright: ignore[reportArgumentType]
            "name": payload.name,
            "description": payload.description,
            "category": payload.category,
            "tags": payload.tags,
            "difficulty": payload.difficulty,
            "estimatedTime": payload.estimated_time,
            "nodes": [
                node.model_dump(by_alias=True)  # pyright: ignore[reportAttributeAccessIssue]
                for node in workflow.nodes
            ],
            "edges": [edge.model_dump(by_alias=True) for edge in workflow.edges],
            "version": payload.version,
            "isTemplate": payload.is_template,
            "isPublic": payload.is_public,
            "userId": "dev",  # anonymous in Phase 1 per ADR-0005
        }
    )
    return WorkflowRead.model_validate(row)


__all__ = ["WorkflowCreate", "WorkflowRead", "router"]
