"""POST /workflows — create a workflow from JSON."""

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, ConfigDict, Field

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.engine.graph_builder import WorkflowValidationError, validate_workflow_shape
from src.engine.workflow import (
    JIRA_TOKEN_REDACTED,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    encrypt_jira_api_token,
    is_jira_api_token_encrypted,
)
from src.security.auth import ensure_admin, get_current_role, get_current_user_id
from src.storage.db import get_db
from src.variable_validation import find_unknown_variable_references

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def _validate_slug(slug: str) -> None:
    if not _SLUG_PATTERN.match(slug):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "external_slug must be lowercase alphanumeric + hyphens, "
                "start with alphanumeric, and be 2-64 chars"
            ),
        )


def _reject_unknown_variable_references(workflow: Workflow) -> None:
    """P0-1: refuse to publish a workflow with a {{variable}} reference
    that doesn't match any Start input, node id/name alias, or built-in
    — an unresolved placeholder renders as literal text at runtime
    (src/variable_substitution.py) instead of failing, so this is the
    only point where a typo like `{{jira_project_key}}` vs. a Start
    input actually named `MB` gets caught before it reaches production."""
    problems = find_unknown_variable_references(workflow)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Workflow references undeclared variables and cannot be published.",
                "problems": [
                    {
                        "nodeId": p.node_id,
                        "field": p.field_path,
                        "placeholder": p.placeholder,
                    }
                    for p in problems
                ],
            },
        )


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


def _redact_jira_tokens(nodes: list[Any]) -> list[Any]:
    """Never let a Jira node's encrypted apiToken leave the server."""
    redacted: list[Any] = []
    for node in nodes:
        if isinstance(node, dict) and node.get("type") == "jira":
            data = dict(node.get("data") or {})
            if data.get("apiToken"):
                data["apiToken"] = JIRA_TOKEN_REDACTED
            node = {**node, "data": data}
        redacted.append(node)
    return redacted


def _to_workflow_read(row: Any) -> "WorkflowRead":
    read = WorkflowRead.model_validate(row)
    read.nodes = _redact_jira_tokens(read.nodes)
    return read


def _encrypt_jira_tokens(nodes_json: list[dict[str, Any]], existing_nodes: list[Any]) -> None:
    """Encrypt plaintext Jira apiToken values in-place before persisting.

    If the incoming value is the redacted marker (the UI echoed back what a
    prior GET returned, unchanged), keep whatever was already stored for that
    node id instead of overwriting it with the literal marker string.
    """
    existing_by_id = {node.get("id"): node for node in existing_nodes if isinstance(node, dict)}
    for node in nodes_json:
        if node.get("type") != "jira":
            continue
        data = node.get("data") or {}
        token = data.get("apiToken")
        if not token:
            continue
        if token == JIRA_TOKEN_REDACTED:
            prior = existing_by_id.get(node.get("id")) or {}
            data["apiToken"] = (prior.get("data") or {}).get("apiToken")
        elif not is_jira_api_token_encrypted(token):
            data["apiToken"] = encrypt_jira_api_token(token)


async def _has_assignment(
    db: Prisma,  # pyright: ignore[reportUnknownParameterType]
    workflow_id: str,
    user_id: str,
) -> bool:
    """True when `user_id` has an active WorkflowAssignment on `workflow_id`.

    Assignment grants full read+write (open, edit, run) but never delete or
    ownership-transfer rights — those stay owner/admin-only (Account +
    Workflow Sharing plan, Part B scope decision).
    """
    row = await db.workflowassignment.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"workflowId_userId": {"workflowId": workflow_id, "userId": user_id}}
    )
    return row is not None


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
    is_production: bool | None = Field(default=None, alias="isProduction")
    external_slug: str | None = Field(default=None, alias="externalSlug")


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
    is_production: bool = Field(default=False, alias="isProduction")
    external_slug: str | None = Field(default=None, alias="externalSlug")
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
    _encrypt_jira_tokens(nodes_json, existing_nodes=[])
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
    return _to_workflow_read(row)


# NOTE: /workflows/search must be registered BEFORE /workflows/{workflow_id}
# so that FastAPI does not swallow "search" as a path parameter.
@router.get("/workflows/search", response_model=WorkflowListResponse)
async def search_workflows(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> WorkflowListResponse:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    text_match: dict[str, Any] = {
        "OR": [
            {"name": {"contains": q, "mode": "insensitive"}},
            {"description": {"contains": q, "mode": "insensitive"}},
        ]
    }
    if role == "admin":
        where: dict[str, Any] = text_match
    else:
        authz: dict[str, Any] = {
            "OR": [
                {"isPublic": True},
                {"userId": user_id},
                {"assignments": {"some": {"userId": user_id}}},
            ]
        }
        where = {"AND": [authz, text_match]}
    total = await db.workflow.count(where=where)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    rows = await db.workflow.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,  # pyright: ignore[reportArgumentType]
        take=limit,
        order={"updatedAt": "desc"},
    )
    items = [_to_workflow_read(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=0)


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    is_template: bool | None = Query(default=None, alias="isTemplate"),
    is_public: bool | None = Query(default=None, alias="isPublic"),
    category: str | None = Query(default=None),
    mine: bool | None = Query(default=None),
) -> WorkflowListResponse:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    # Authz / scope:
    #   mine=true (any role) → strictly "workflows I own".  Admins
    #     asking for their own workflows shouldn't get the global feed.
    #   admin without mine → see all workflows (the original
    #     admin-sees-everything behaviour).
    #   non-admin without mine → public + own (discovery feed).
    assignment_clause: dict[str, Any] = {"assignments": {"some": {"userId": user_id}}}
    authz_where: dict[str, Any] | None
    if mine:
        authz_where = {"OR": [{"userId": user_id}, assignment_clause]}
    elif role == "admin":
        authz_where = None
    else:
        authz_where = {"OR": [{"isPublic": True}, {"userId": user_id}, assignment_clause]}

    filter_conditions: list[dict[str, Any]] = []
    if is_template is not None:
        filter_conditions.append({"isTemplate": is_template})
    if is_public is not None:
        filter_conditions.append({"isPublic": is_public})
    if category is not None:
        filter_conditions.append({"category": category})

    where: dict[str, Any] | None
    if authz_where is None:
        where = {"AND": filter_conditions} if filter_conditions else None
    elif filter_conditions:
        where = {"AND": [authz_where, *filter_conditions]}
    else:
        where = authz_where

    total = await db.workflow.count(where=where)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    rows = await db.workflow.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,  # pyright: ignore[reportArgumentType]
        take=limit,
        skip=offset,
        order={"updatedAt": "desc"},
    )
    items = [_to_workflow_read(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=offset)


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    row = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if role == "admin":
        return _to_workflow_read(row)
    if row.isPublic or row.userId == user_id:
        return _to_workflow_read(row)
    if await _has_assignment(db, workflow_id, user_id):
        return _to_workflow_read(row)
    # 404 for private, not owner, not assignee — info-leak tight (ADR-0021)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Workflow {workflow_id!r} not found.",
    )


@router.put("/workflows/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowCreate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if (
        role != "admin"
        and existing.userId != user_id
        and not await _has_assignment(db, workflow_id, user_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner or assignee.",
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
    existing_nodes_raw = existing.nodes
    existing_nodes: list[Any] = existing_nodes_raw if isinstance(existing_nodes_raw, list) else []
    _encrypt_jira_tokens(nodes_json, existing_nodes=existing_nodes)
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]

    # Publish / unpublish handling
    publish_data: dict[str, Any] = {}
    if payload.is_production is True:
        if not payload.external_slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="external_slug is required when isProduction=True",
            )
        _validate_slug(payload.external_slug)
        _reject_unknown_variable_references(workflow)
        publish_data["isProduction"] = True
        publish_data["externalSlug"] = payload.external_slug
    elif payload.is_production is False:
        publish_data["isProduction"] = False
        publish_data["externalSlug"] = None

    update_data: dict[str, Any] = {
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
        **publish_data,
    }

    try:
        updated = await db.workflow.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": workflow_id},
            data=update_data,  # pyright: ignore[reportArgumentType]
        )
    except UniqueViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"external_slug already in use: {exc}",
        ) from exc
    return _to_workflow_read(updated)


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> None:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if role != "admin" and existing.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner.",
        )
    await db.workflow.delete(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}
    )


class OwnerAssignRequest(BaseModel):
    user_id: str | None = Field(default=None, alias="userId")
    email: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class WorkflowAssignmentRead(BaseModel):
    """Response body for workflow assignment CRUD endpoints."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    workflow_id: str = Field(alias="workflowId")
    user_id: str = Field(alias="userId")
    assigned_by_id: str = Field(alias="assignedById")
    assigned_at: Any = Field(alias="assignedAt")


class AdminFlagsRequest(BaseModel):
    """Admin-only partial update for visibility / production flags."""

    is_public: bool | None = Field(default=None, alias="isPublic")
    is_production: bool | None = Field(default=None, alias="isProduction")
    external_slug: str | None = Field(default=None, alias="externalSlug")

    model_config = ConfigDict(populate_by_name=True)


@router.patch("/workflows/{workflow_id}/admin-flags", response_model=WorkflowRead)
async def admin_update_workflow_flags(
    workflow_id: str,
    payload: AdminFlagsRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")

    data: dict[str, Any] = {}
    if payload.is_public is not None:
        data["isPublic"] = payload.is_public
    if payload.is_production is True:
        slug = payload.external_slug or getattr(existing, "externalSlug", None)
        if not slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="external_slug is required when isProduction=True",
            )
        _validate_slug(slug)
        _reject_unknown_variable_references(
            Workflow.model_validate(
                {"name": existing.name, "nodes": existing.nodes, "edges": existing.edges}
            )
        )
        data["isProduction"] = True
        data["externalSlug"] = slug
    elif payload.is_production is False:
        data["isProduction"] = False
        data["externalSlug"] = None
    elif payload.external_slug is not None:
        # Slug change without flipping isProduction
        _validate_slug(payload.external_slug)
        data["externalSlug"] = payload.external_slug

    if not data:
        return _to_workflow_read(existing)

    try:
        updated = await db.workflow.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": workflow_id},
            data=data,  # pyright: ignore[reportArgumentType]
        )
    except UniqueViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"external_slug already in use: {exc}",
        ) from exc
    return _to_workflow_read(updated)


@router.patch("/workflows/{workflow_id}/owner", response_model=WorkflowRead)
async def assign_workflow_owner(
    workflow_id: str,
    payload: OwnerAssignRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    target_user_id = payload.user_id
    if target_user_id is None:
        if not payload.email:
            raise HTTPException(422, "must provide user_id or email")
        target_user = await db.user.find_unique(where={"email": payload.email.lower()})  # pyright: ignore[reportAttributeAccessIssue]
        if target_user is None:
            raise HTTPException(404, f"user with email {payload.email!r} not found")
        target_user_id = target_user.id
    else:
        target_user = await db.user.find_unique(where={"id": target_user_id})  # pyright: ignore[reportAttributeAccessIssue]
        if target_user is None:
            raise HTTPException(404, f"user {target_user_id!r} not found")

    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")

    updated = await db.workflow.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id}, data={"userId": target_user_id}
    )
    return _to_workflow_read(updated)


@router.get("/workflows/{workflow_id}/assignments", response_model=list[WorkflowAssignmentRead])
async def list_workflow_assignments(
    workflow_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> list[WorkflowAssignmentRead]:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.userId != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    rows = await db.workflowassignment.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"workflowId": workflow_id}
    )
    return [WorkflowAssignmentRead.model_validate(r) for r in rows]


@router.post(
    "/workflows/{workflow_id}/assignments/{target_user_id}",
    response_model=WorkflowAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def grant_workflow_assignment(
    workflow_id: str,
    target_user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowAssignmentRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.userId != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    target_user = await db.user.find_unique(where={"id": target_user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if target_user is None:
        raise HTTPException(404, f"user {target_user_id!r} not found")
    try:
        row = await db.workflowassignment.create(  # pyright: ignore[reportAttributeAccessIssue]
            data={
                "workflowId": workflow_id,
                "userId": target_user_id,
                "assignedById": user_id,
            }
        )
    except UniqueViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"assignment already exists for user {target_user_id!r}: {exc}",
        ) from exc
    return WorkflowAssignmentRead.model_validate(row)


@router.delete(
    "/workflows/{workflow_id}/assignments/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_workflow_assignment(
    workflow_id: str,
    target_user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _role: tuple[str, str] = Depends(get_current_role),
) -> None:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.userId != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    deleted = await db.workflowassignment.delete(  # pyright: ignore[reportAttributeAccessIssue]
        where={"workflowId_userId": {"workflowId": workflow_id, "userId": target_user_id}}
    )
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"assignment not found for user {target_user_id!r}",
        )


__all__ = [
    "OwnerAssignRequest",
    "WorkflowAssignmentRead",
    "WorkflowCreate",
    "WorkflowListResponse",
    "WorkflowRead",
    "router",
]
