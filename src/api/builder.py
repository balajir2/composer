"""POST /builder/{workflowId}/message — the natural-language workflow builder."""

import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.api.workflows import (  # reuse existing secret helpers (additive import)
    _check_workflow_size_limits,  # pyright: ignore[reportPrivateUsage]
    _encrypt_confluence_tokens,  # pyright: ignore[reportPrivateUsage]
    _encrypt_http_headers,  # pyright: ignore[reportPrivateUsage]
    _encrypt_jira_tokens,  # pyright: ignore[reportPrivateUsage]
    _encrypt_vector_db_keys,  # pyright: ignore[reportPrivateUsage]
    _to_workflow_read,  # pyright: ignore[reportPrivateUsage]
)
from src.builder.builder_agent import BuildSession, run_builder_turn
from src.builder.model_policy import ModelNotAllowedError, enabled_model_catalog, load_policy
from src.config import get_settings
from src.db.session import get_session
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import Workflow
from src.llm.providers import build_chat_model
from src.security.auth import get_current_role, get_current_user_id
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db


async def _has_assignment(db: Prisma, workflow_id: str, user_id: str) -> bool:
    """Prisma assignment check.

    TODO(phase 3 task 4): the builder moves to SQLAlchemy; delete this and use
    ``workflow_assignments.exists``.
    """
    row = await db.workflowassignment.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"workflowId_userId": {"workflowId": workflow_id, "userId": user_id}}
    )
    return row is not None


router = APIRouter(tags=["builder"])

SESSION_TTL_SECONDS = 30 * 60


class BuilderMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: str | None = Field(default=None, alias="sessionId")
    execution_id: str | None = Field(default=None, alias="executionId")
    # "provider/modelId"; must be in the admin-approved set. Omit for the default.
    model: str | None = None


class BuilderModelOption(BaseModel):
    id: str
    label: str


class BuilderModelsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default: str
    models: list[BuilderModelOption]


class BuilderMessageResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    events: list[dict[str, Any]]
    reply: str
    workflow: Any
    validation_errors: list[str]


def _get_sessions(request: Request) -> dict[str, BuildSession]:
    sessions = getattr(request.app.state, "builder_sessions", None)
    if sessions is None:
        sessions = {}
        request.app.state.builder_sessions = sessions
    return sessions


def _prune_sessions(sessions: dict[str, BuildSession]) -> None:
    now = time.monotonic()
    for sid in [
        s for s, sess in sessions.items() if now - sess.last_activity > SESSION_TTL_SECONDS
    ]:
        del sessions[sid]


async def _load_workflow(db: Prisma, workflow_id: str) -> Any:
    row = await db.workflow.find_unique(where={"id": workflow_id})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    return row


def _to_engine_workflow(row: Any) -> Workflow:
    return Workflow.model_validate(
        {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "nodes": row.nodes if isinstance(row.nodes, list) else [],
            "edges": row.edges if isinstance(row.edges, list) else [],
        }
    )


async def _persist(db: Prisma, workflow_id: str, workflow: Workflow, existing: Any) -> Any:
    nodes_json = [node.model_dump(by_alias=True) for node in workflow.nodes]
    existing_nodes = existing.nodes if isinstance(existing.nodes, list) else []
    _encrypt_jira_tokens(nodes_json, existing_nodes=existing_nodes)
    _encrypt_vector_db_keys(nodes_json, existing_nodes=existing_nodes)
    _encrypt_http_headers(nodes_json, existing_nodes=existing_nodes)
    _encrypt_confluence_tokens(nodes_json, existing_nodes=existing_nodes)
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]
    # Metadata ruling: update ONLY nodes + edges; never the metadata columns.
    return await db.workflow.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": workflow_id},
        data={"nodes": Json(nodes_json), "edges": Json(edges_json)},  # pyright: ignore[reportArgumentType]
    )


@router.get("/builder/models", response_model=BuilderModelsResponse)
async def builder_models(  # pyright: ignore[reportUnusedFunction]
    sa_session: AsyncSession = Depends(get_session),
    _user_id: str = Depends(get_current_user_id),
) -> BuilderModelsResponse:
    """Models the signed-in user may pick in the Build-with-AI panel."""
    policy = await load_policy(sa_session)
    return BuilderModelsResponse(
        default=policy.default,
        models=[BuilderModelOption(id=o.id, label=o.label) for o in policy.options],
    )


@router.post("/builder/{workflow_id}/message", response_model=BuilderMessageResponse)
async def builder_message(  # pyright: ignore[reportUnusedFunction]
    workflow_id: str,
    payload: BuilderMessageRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    # TODO(prisma removal): merge db and sa_session
    sa_session: AsyncSession = Depends(get_session),
    _role: tuple[str, str] = Depends(get_current_role),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> BuilderMessageResponse:
    user_id, role = _role
    await enforce(
        limiter,
        route_key="builder",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_builder_per_minute),
    )

    # R4: session check BEFORE loading the workflow (expired session needs no DB read).
    sessions = _get_sessions(request)
    _prune_sessions(sessions)

    session = sessions.get(payload.session_id) if payload.session_id else None
    if payload.session_id and session is None:
        return BuilderMessageResponse(
            sessionId=payload.session_id,
            events=[{"type": "session_expired"}],
            reply="This build session has expired. Please start over.",
            workflow=None,
            validation_errors=[],
        )

    existing = await _load_workflow(db, workflow_id)
    if (
        role != "admin"
        and existing.userId != user_id
        and not await _has_assignment(db, workflow_id, user_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found.")

    if session is None:
        sid = secrets.token_hex(16)
        session = BuildSession(workflow_id=workflow_id)
        sessions[sid] = session
    else:
        sid = payload.session_id or ""

    policy = await load_policy(sa_session)
    try:
        model_string = policy.resolve(payload.model)
    except ModelNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    model = build_chat_model(model_string)

    workflow = _to_engine_workflow(existing)
    model_catalog = await enabled_model_catalog(sa_session)
    # Release the pooled connection before the slow LLM loop: reads autobegin a
    # transaction that would otherwise pin it until the request ends.
    await sa_session.rollback()
    events, reply, updated = await run_builder_turn(
        model=model,
        workflow_id=workflow_id,
        workflow=workflow,
        session=session,
        message=payload.message,
        execution_id=payload.execution_id,
        db=db,
        model_catalog=model_catalog,
    )

    _check_workflow_size_limits(updated)
    row = await _persist(db, workflow_id, updated, existing)
    validation_errors = collect_workflow_violations(updated)
    return BuilderMessageResponse(
        sessionId=sid,
        events=events,
        reply=reply,
        workflow=_to_workflow_read(row),
        validation_errors=validation_errors,
    )
