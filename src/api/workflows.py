"""POST /workflows — create a workflow from JSON."""

import re
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.errors import AlreadyExists, ReferencedRowMissing
from src.db.repos import users as users_repo
from src.db.repos import workflow_assignments as assignments_repo
from src.db.repos import workflows as workflows_repo
from src.db.repos.workflows import WorkflowChanges, WorkflowRecord
from src.db.session import get_session
from src.engine.graph_builder import (
    WorkflowValidationError,
    dedupe_edges,
    validate_workflow_shape,
)
from src.engine.workflow import (
    JIRA_TOKEN_REDACTED,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    encrypt_jira_api_token,
    is_jira_api_token_encrypted,
)
from src.security.auth import ensure_admin, get_current_role, get_current_user_id
from src.security.encryption import (
    REDACTED_MARKER,
    encrypt_marked,
    encrypt_sensitive_headers,
    is_marked_encrypted,
    redact_sensitive_headers,
)
from src.variable_validation import find_unknown_variable_references

# P0-5: vector-db node fields carrying secret API keys, same class of gap
# Jira's apiToken already had closed (ADR-0028) — encrypt at rest, redact
# on every read.
_VECTOR_DB_SECRET_FIELDS = ("vectorDbApiKey", "vectorDbEmbeddingApiKey")

# Confluence node's apiToken field: same encryption-at-rest,
# redaction-on-read pattern (Phase 2, confluence-node feature).
_CONFLUENCE_SECRET_FIELDS = ("apiToken",)

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def _validate_slug(slug: str) -> None:
    if not _SLUG_PATTERN.match(slug):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"workflow exceeds max_nodes={settings.max_workflow_nodes}; "
                f"got {len(workflow.nodes)}"
            ),
        )
    if len(workflow.edges) > settings.max_workflow_edges:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"workflow exceeds max_edges={settings.max_workflow_edges}; "
                f"got {len(workflow.edges)}"
            ),
        )


def _redact_jira_tokens(nodes: list[Any]) -> list[Any]:
    """Never let a Jira node's encrypted apiToken leave the server."""
    redacted: list[Any] = []
    for node in nodes:
        if isinstance(node, dict):
            node = cast("dict[str, Any]", node)
            if node.get("type") == "jira":
                data = dict(node.get("data") or {})
                if data.get("apiToken"):
                    data["apiToken"] = JIRA_TOKEN_REDACTED
                node = {**node, "data": data}
        redacted.append(node)
    return redacted


def _redact_vector_db_keys(nodes: list[Any]) -> list[Any]:
    """Never let a vector-db node's encrypted apiKey/embeddingApiKey leave
    the server (P0-5) — same treatment as _redact_jira_tokens above."""
    redacted: list[Any] = []
    for node in nodes:
        if isinstance(node, dict):
            node = cast("dict[str, Any]", node)
            if node.get("type") == "vector-db":
                data = dict(node.get("data") or {})
                for field in _VECTOR_DB_SECRET_FIELDS:
                    if data.get(field):
                        data[field] = REDACTED_MARKER
                node = {**node, "data": data}
        redacted.append(node)
    return redacted


def _redact_confluence_tokens(nodes: list[Any]) -> list[Any]:
    """Never let a confluence node's encrypted apiToken leave the server —
    same treatment as _redact_vector_db_keys above."""
    redacted: list[Any] = []
    for node in nodes:
        if isinstance(node, dict):
            node = cast("dict[str, Any]", node)
            if node.get("type") == "confluence":
                data = dict(node.get("data") or {})
                for field in _CONFLUENCE_SECRET_FIELDS:
                    if data.get(field):
                        data[field] = REDACTED_MARKER
                node = {**node, "data": data}
        redacted.append(node)
    return redacted


def _redact_http_headers(nodes: list[Any]) -> list[Any]:
    """Never let an http node's encrypted sensitive header values (e.g.
    Authorization) leave the server (P0-5) — same treatment as
    _redact_jira_tokens above, applied per-header-name via
    redact_sensitive_headers rather than a single fixed field."""
    redacted: list[Any] = []
    for node in nodes:
        if isinstance(node, dict):
            node = cast("dict[str, Any]", node)
            if node.get("type") == "http":
                data = dict(node.get("data") or {})
                headers = data.get("httpHeaders")
                if isinstance(headers, dict):
                    headers = cast("dict[str, Any]", headers)
                    data["httpHeaders"] = redact_sensitive_headers(headers)
                node = {**node, "data": data}
        redacted.append(node)
    return redacted


def _to_workflow_read(row: Any) -> "WorkflowRead":
    read = WorkflowRead.model_validate(row)
    read.nodes = _redact_jira_tokens(read.nodes)
    read.nodes = _redact_vector_db_keys(read.nodes)
    read.nodes = _redact_http_headers(read.nodes)
    read.nodes = _redact_confluence_tokens(read.nodes)
    return read


def _encrypt_jira_tokens(nodes_json: list[dict[str, Any]], existing_nodes: list[Any]) -> None:
    """Encrypt plaintext Jira apiToken values in-place before persisting.

    If the incoming value is the redacted marker (the UI echoed back what a
    prior GET returned, unchanged), keep whatever was already stored for that
    node id instead of overwriting it with the literal marker string.
    """
    existing_by_id: dict[Any, dict[str, Any]] = {
        cast("dict[str, Any]", node).get("id"): cast("dict[str, Any]", node)
        for node in existing_nodes
        if isinstance(node, dict)
    }
    for node in nodes_json:
        if node.get("type") != "jira":
            continue
        data: dict[str, Any] = node.get("data") or {}
        token = data.get("apiToken")
        if not token:
            continue
        if token == JIRA_TOKEN_REDACTED:
            prior = existing_by_id.get(node.get("id")) or {}
            prior_data: dict[str, Any] = prior.get("data") or {}
            data["apiToken"] = prior_data.get("apiToken")
        elif not is_jira_api_token_encrypted(token):
            data["apiToken"] = encrypt_jira_api_token(token)


def _encrypt_vector_db_keys(nodes_json: list[dict[str, Any]], existing_nodes: list[Any]) -> None:
    """Encrypt plaintext vector-db apiKey/embeddingApiKey values in-place
    before persisting (P0-5) — same preserve-on-redacted-marker treatment
    as _encrypt_jira_tokens above."""
    existing_by_id: dict[Any, dict[str, Any]] = {
        cast("dict[str, Any]", node).get("id"): cast("dict[str, Any]", node)
        for node in existing_nodes
        if isinstance(node, dict)
    }
    for node in nodes_json:
        if node.get("type") != "vector-db":
            continue
        data: dict[str, Any] = node.get("data") or {}
        for field in _VECTOR_DB_SECRET_FIELDS:
            value = data.get(field)
            if not value:
                continue
            if value == REDACTED_MARKER:
                prior = existing_by_id.get(node.get("id")) or {}
                prior_data: dict[str, Any] = prior.get("data") or {}
                data[field] = prior_data.get(field)
            elif not is_marked_encrypted(value):
                data[field] = encrypt_marked(value)


def _encrypt_confluence_tokens(nodes_json: list[dict[str, Any]], existing_nodes: list[Any]) -> None:
    """Encrypt plaintext confluence apiToken values in-place before
    persisting — same preserve-on-redacted-marker treatment as
    _encrypt_vector_db_keys above."""
    existing_by_id: dict[Any, dict[str, Any]] = {
        cast("dict[str, Any]", node).get("id"): cast("dict[str, Any]", node)
        for node in existing_nodes
        if isinstance(node, dict)
    }
    for node in nodes_json:
        if node.get("type") != "confluence":
            continue
        data: dict[str, Any] = node.get("data") or {}
        for field in _CONFLUENCE_SECRET_FIELDS:
            value = data.get(field)
            if not value:
                continue
            if value == REDACTED_MARKER:
                prior = existing_by_id.get(node.get("id")) or {}
                prior_data: dict[str, Any] = prior.get("data") or {}
                data[field] = prior_data.get(field)
            elif not is_marked_encrypted(value):
                data[field] = encrypt_marked(value)


def _encrypt_http_headers(nodes_json: list[dict[str, Any]], existing_nodes: list[Any]) -> None:
    """Encrypt plaintext sensitive http-node header values in-place before
    persisting (P0-5) — same preserve-on-redacted-marker treatment as
    _encrypt_jira_tokens above, applied per-header-name."""
    existing_by_id: dict[Any, dict[str, Any]] = {
        cast("dict[str, Any]", node).get("id"): cast("dict[str, Any]", node)
        for node in existing_nodes
        if isinstance(node, dict)
    }
    for node in nodes_json:
        if node.get("type") != "http":
            continue
        data: dict[str, Any] = node.get("data") or {}
        headers = data.get("httpHeaders")
        if not isinstance(headers, dict):
            continue
        headers = cast("dict[str, Any]", headers)
        prior = existing_by_id.get(node.get("id")) or {}
        prior_data: dict[str, Any] = prior.get("data") or {}
        prior_headers = prior_data.get("httpHeaders")
        resolved_prior_headers = (
            cast("dict[str, Any]", prior_headers) if isinstance(prior_headers, dict) else None
        )
        data["httpHeaders"] = encrypt_sensitive_headers(
            headers, prior_headers=resolved_prior_headers
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


async def _update_or_409(
    session: AsyncSession, workflow_id: str, changes: WorkflowChanges
) -> WorkflowRecord:
    """Update a workflow; a slug clash is a 409 whose detail names only the constraint."""
    try:
        updated = await workflows_repo.update(session, workflow_id, changes)
    except AlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"external_slug already in use: {exc}",
        ) from exc
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    return updated


@router.post("/workflows", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    # Re-validate as Workflow to run graph_builder's shape check.
    workflow = Workflow.model_validate(payload.model_dump(by_alias=True))
    workflow = workflow.model_copy(update={"edges": dedupe_edges(workflow.edges)})
    try:
        validate_workflow_shape(workflow)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    _check_workflow_size_limits(workflow)

    nodes_json = [
        node.model_dump(by_alias=True)  # pyright: ignore[reportAttributeAccessIssue]
        for node in workflow.nodes
    ]
    _encrypt_jira_tokens(nodes_json, existing_nodes=[])
    _encrypt_vector_db_keys(nodes_json, existing_nodes=[])
    _encrypt_http_headers(nodes_json, existing_nodes=[])
    _encrypt_confluence_tokens(nodes_json, existing_nodes=[])
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]
    row = await workflows_repo.create(
        session,
        {
            "name": payload.name,
            "description": payload.description,
            "category": payload.category,
            "tags": payload.tags,
            "difficulty": payload.difficulty,
            "estimated_time": payload.estimated_time,
            "nodes": nodes_json,
            "edges": edges_json,
            "version": payload.version,
            "is_template": payload.is_template,
            "is_public": payload.is_public,
            "user_id": user_id,
        },
    )
    await session.commit()
    return _to_workflow_read(row)


# NOTE: /workflows/search must be registered BEFORE /workflows/{workflow_id}
# so that FastAPI does not swallow "search" as a path parameter.
@router.get("/workflows/search", response_model=WorkflowListResponse)
async def search_workflows(
    session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> WorkflowListResponse:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    rows, total = await workflows_repo.search(
        session, viewer={"user_id": user_id, "is_admin": role == "admin"}, q=q, limit=limit
    )
    items = [_to_workflow_read(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=0)


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    session: AsyncSession = Depends(get_session),
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
    #   mine=true (any role) -> strictly "workflows I own or am assigned".  Admins
    #     asking for their own workflows shouldn't get the global feed.
    #   admin without mine -> see all workflows (the original
    #     admin-sees-everything behaviour).
    #   non-admin without mine -> public + own + assigned (discovery feed).
    rows, total = await workflows_repo.list_page(
        session,
        viewer={"user_id": user_id, "is_admin": role == "admin"},
        mine=bool(mine),
        is_template=is_template,
        is_public=is_public,
        category=category,
        limit=limit,
        offset=offset,
    )
    items = [_to_workflow_read(row) for row in rows]
    return WorkflowListResponse(total=total, items=items, limit=limit, offset=offset)


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    row = await workflows_repo.get(session, workflow_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if role == "admin":
        return _to_workflow_read(row)
    if row.is_public or row.user_id == user_id:
        return _to_workflow_read(row)
    if await assignments_repo.exists(session, workflow_id, user_id):
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
    session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowRead:
    user_id, role = _role
    existing = await workflows_repo.get(session, workflow_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if (
        role != "admin"
        and existing.user_id != user_id
        and not await assignments_repo.exists(session, workflow_id, user_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner or assignee.",
        )

    workflow = Workflow.model_validate(payload.model_dump(by_alias=True))
    workflow = workflow.model_copy(update={"edges": dedupe_edges(workflow.edges)})
    try:
        validate_workflow_shape(workflow)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    _check_workflow_size_limits(workflow)

    nodes_json = [
        node.model_dump(by_alias=True)  # pyright: ignore[reportAttributeAccessIssue]
        for node in workflow.nodes
    ]
    existing_nodes: list[Any] = existing.nodes
    _encrypt_jira_tokens(nodes_json, existing_nodes=existing_nodes)
    _encrypt_vector_db_keys(nodes_json, existing_nodes=existing_nodes)
    _encrypt_http_headers(nodes_json, existing_nodes=existing_nodes)
    _encrypt_confluence_tokens(nodes_json, existing_nodes=existing_nodes)
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]

    # Publish / unpublish handling
    publish_data: WorkflowChanges = {}
    if payload.is_production is True:
        if not payload.external_slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="external_slug is required when isProduction=True",
            )
        _validate_slug(payload.external_slug)
        _reject_unknown_variable_references(workflow)
        publish_data["is_production"] = True
        publish_data["external_slug"] = payload.external_slug
    elif payload.is_production is False:
        publish_data["is_production"] = False
        publish_data["external_slug"] = None

    changes: WorkflowChanges = {
        "name": payload.name,
        "description": payload.description,
        "category": payload.category,
        "tags": payload.tags,
        "difficulty": payload.difficulty,
        "estimated_time": payload.estimated_time,
        "nodes": nodes_json,
        "edges": edges_json,
        "version": payload.version,
        "is_template": payload.is_template,
        "is_public": payload.is_public,
        **publish_data,
    }

    updated = await _update_or_409(session, workflow_id, changes)
    await session.commit()
    return _to_workflow_read(updated)


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
) -> None:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await workflows_repo.get(session, workflow_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if role != "admin" and existing.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: not workflow owner.",
        )
    await workflows_repo.delete(session, workflow_id)
    await session.commit()


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
    assigned_by_id: str | None = Field(alias="assignedById")
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
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    existing = await workflows_repo.get(session, workflow_id)
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")

    data: WorkflowChanges = {}
    if payload.is_public is not None:
        data["is_public"] = payload.is_public
    if payload.is_production is True:
        slug = payload.external_slug or existing.external_slug
        if not slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="external_slug is required when isProduction=True",
            )
        _validate_slug(slug)
        _reject_unknown_variable_references(
            Workflow.model_validate(
                {"name": existing.name, "nodes": existing.nodes, "edges": existing.edges}
            )
        )
        data["is_production"] = True
        data["external_slug"] = slug
    elif payload.is_production is False:
        data["is_production"] = False
        data["external_slug"] = None
    elif payload.external_slug is not None:
        # Slug change without flipping isProduction
        _validate_slug(payload.external_slug)
        data["external_slug"] = payload.external_slug

    if not data:
        return _to_workflow_read(existing)

    updated = await _update_or_409(session, workflow_id, data)
    await session.commit()
    return _to_workflow_read(updated)


@router.patch("/workflows/{workflow_id}/owner", response_model=WorkflowRead)
async def assign_workflow_owner(
    workflow_id: str,
    payload: OwnerAssignRequest,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> WorkflowRead:  # pyright: ignore[reportUnusedFunction]
    # One transaction: the user lookup, the workflow read and the write share this
    # session and the single commit below.
    target_user_id = payload.user_id
    if target_user_id is None:
        if not payload.email:
            raise HTTPException(422, "must provide user_id or email")
        target_user = await users_repo.get_by_email(session, payload.email.lower())
        if target_user is None:
            raise HTTPException(404, f"user with email {payload.email!r} not found")
        target_user_id = target_user.id
    else:
        target_user = await users_repo.get_by_id(session, target_user_id)
        if target_user is None:
            raise HTTPException(404, f"user {target_user_id!r} not found")

    existing = await workflows_repo.get(session, workflow_id)
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")

    updated = await workflows_repo.update(session, workflow_id, {"user_id": target_user_id})
    if updated is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    await session.commit()
    return _to_workflow_read(updated)


@router.get("/workflows/{workflow_id}/assignments", response_model=list[WorkflowAssignmentRead])
async def list_workflow_assignments(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
) -> list[WorkflowAssignmentRead]:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await workflows_repo.get(session, workflow_id)
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.user_id != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    rows = await assignments_repo.list_for_workflow(session, workflow_id)
    return [WorkflowAssignmentRead.model_validate(r) for r in rows]


_ASSIGNMENT_USER_FKS = frozenset(
    {"workflow_assignments_user_id_fkey", "workflow_assignments_assigned_by_id_fkey"}
)


@router.post(
    "/workflows/{workflow_id}/assignments/{target_user_id}",
    response_model=WorkflowAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def grant_workflow_assignment(
    workflow_id: str,
    target_user_id: str,
    session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
) -> WorkflowAssignmentRead:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await workflows_repo.get(session, workflow_id, for_update=True)
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.user_id != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    target_user = await users_repo.get_by_id(session, target_user_id)
    if target_user is None:
        raise HTTPException(404, f"user {target_user_id!r} not found")
    try:
        row = await assignments_repo.create(
            session, workflow_id=workflow_id, user_id=target_user_id, assigned_by_id=user_id
        )
    except ReferencedRowMissing as exc:
        # The workflow row is locked, so the reachable case is a user (the target or
        # the grantor) deleted after the checks above; anything else is the workflow.
        if exc.constraint in _ASSIGNMENT_USER_FKS:
            raise HTTPException(404, f"user {target_user_id!r} not found") from exc
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.") from exc
    except AlreadyExists as exc:
        # str(exc) names the constraint only: no SQL, parameters or ciphertext.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"assignment already exists for user {target_user_id!r}: {exc}",
        ) from exc
    await session.commit()
    return WorkflowAssignmentRead.model_validate(row)


@router.delete(
    "/workflows/{workflow_id}/assignments/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_workflow_assignment(
    workflow_id: str,
    target_user_id: str,
    session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
) -> None:  # pyright: ignore[reportUnusedFunction]
    user_id, role = _role
    existing = await workflows_repo.get(session, workflow_id, for_update=True)
    if existing is None:
        raise HTTPException(404, f"Workflow {workflow_id!r} not found.")
    if role != "admin" and existing.user_id != user_id:
        raise HTTPException(403, "Forbidden: not workflow owner.")
    if not await assignments_repo.delete(session, workflow_id, target_user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"assignment not found for user {target_user_id!r}",
        )
    await session.commit()


__all__ = [
    "OwnerAssignRequest",
    "WorkflowAssignmentRead",
    "WorkflowCreate",
    "WorkflowListResponse",
    "WorkflowRead",
    "router",
]
