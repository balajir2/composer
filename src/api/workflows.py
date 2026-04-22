"""POST /workflows — create a workflow from JSON."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.engine.graph_builder import WorkflowValidationError, validate_workflow_shape
from src.engine.workflow import Workflow, WorkflowEdge, WorkflowNode
from src.security.auth import get_current_user_id
from src.storage.db import get_db

router = APIRouter(tags=["workflows"])


def _check_workflow_size_limits(workflow: Workflow) -> None:
    settings = get_settings()
    if len(workflow.nodes) > settings.max_workflow_nodes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"workflow exceeds max_nodes={settings.max_workflow_nodes}; "
                f"got {len(workflow.nodes)}"
            ),
        )
    if len(workflow.edges) > settings.max_workflow_edges:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"workflow exceeds max_edges={settings.max_workflow_edges}; "
                f"got {len(workflow.edges)}"
            ),
        )


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


class WorkflowListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    items: list[WorkflowRead]
    limit: int
    offset: int


@router.post("/workflows", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    # Re-validate as Workflow to run graph_builder's shape check.
    workflow = Workflow.model_validate(payload.model_dump(by_alias=True))
    try:
        validate_workflow_shape(workflow)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    _check_workflow_size_limits(workflow)

    nodes_json = [
        node.model_dump(by_alias=True)  # pyright: ignore[reportAttributeAccessIssue]
        for node in workflow.nodes
    ]
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]
    row = await db.workflow.create(
        data={  # pyright: ignore[reportArgumentType]
            "name": payload.name,
            "description": payload.description,
            "category": payload.category,
            "tags": payload.tags,
            "difficulty": payload.difficulty,
            "estimatedTime": payload.estimated_time,
            "nodes": Json(nodes_json),
            "edges": Json(edges_json),
            "version": payload.version,
            "isTemplate": payload.is_template,
            "isPublic": payload.is_public,
            "userId": user_id,
        }
    )
    return WorkflowRead.model_validate(row)


# NOTE: /workflows/search must be registered BEFORE /workflows/{workflow_id}
# so that FastAPI does not swallow "search" as a path parameter.
@router.get("/workflows/search", response_model=WorkflowListResponse)
async def search_workflows(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> WorkflowListResponse:  # pyright: ignore[reportUnusedFunction]
    text_match: dict[str, Any] = {
        "OR": [
            {"name": {"contains": q, "mode": "insensitive"}},
            {"description": {"contains": q, "mode": "insensitive"}},
        ]
    }
    authz: dict[str, Any] = {"OR": [{"isPublic": True}, {"userId": user_id}]}
    where: dict[str, Any] = {"AND": [authz, text_match]}
    total = await db.workflow.count(where=where)  # pyright: ignore[reportAttributeAccessIssue]
    rows = await db.workflow.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,
        take=limit,
        order={"updatedAt": "desc"},
    )
    items = [WorkflowRead.model_validate(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=0)


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    is_template: bool | None = Query(default=None, alias="isTemplate"),
    is_public: bool | None = Query(default=None, alias="isPublic"),
    category: str | None = Query(default=None),
    mine: bool | None = Query(default=None),
) -> WorkflowListResponse:  # pyright: ignore[reportUnusedFunction]
    # Authz: public-OR-owned by default.  `mine=true` restricts to owned only.
    authz_where: dict[str, Any] = (
        {"userId": user_id} if mine else {"OR": [{"isPublic": True}, {"userId": user_id}]}
    )

    filter_conditions: list[dict[str, Any]] = []
    if is_template is not None:
        filter_conditions.append({"isTemplate": is_template})
    if is_public is not None:
        filter_conditions.append({"isPublic": is_public})
    if category is not None:
        filter_conditions.append({"category": category})

    if filter_conditions:
        where: dict[str, Any] = {"AND": [authz_where, *filter_conditions]}
    else:
        where = authz_where

    total = await db.workflow.count(where=where)  # pyright: ignore[reportAttributeAccessIssue]
    rows = await db.workflow.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,
        take=limit,
        skip=offset,
        order={"updatedAt": "desc"},
    )
    items = [WorkflowRead.model_validate(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=offset)


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    row = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if row is None or (not row.isPublic and row.userId != user_id):
        # 404 for "not found" AND "private, not owner" — info-leak tight (ADR-0021)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    return WorkflowRead.model_validate(row)


@router.put("/workflows/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowCreate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    existing = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if existing.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner.",
        )

    workflow = Workflow.model_validate(payload.model_dump(by_alias=True))
    try:
        validate_workflow_shape(workflow)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    _check_workflow_size_limits(workflow)

    nodes_json = [
        node.model_dump(by_alias=True)  # pyright: ignore[reportAttributeAccessIssue]
        for node in workflow.nodes
    ]
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]
    updated = await db.workflow.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id},
        data={
            "name": payload.name,
            "description": payload.description,
            "category": payload.category,
            "tags": payload.tags,
            "difficulty": payload.difficulty,
            "estimatedTime": payload.estimated_time,
            "nodes": Json(nodes_json),
            "edges": Json(edges_json),
            "version": payload.version,
            "isTemplate": payload.is_template,
            "isPublic": payload.is_public,
        },
    )
    return WorkflowRead.model_validate(updated)


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> None:  # pyright: ignore[reportUnusedFunction]
    existing = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if existing.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner.",
        )
    await db.workflow.delete(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )


__all__ = ["WorkflowCreate", "WorkflowListResponse", "WorkflowRead", "router"]
