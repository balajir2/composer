"""POST /builder/{workflowId}/message — the natural-language workflow builder."""

import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.api.workflows import (  # reuse existing secret helpers (additive import)
    _check_workflow_size_limits,  # pyright: ignore[reportPrivateUsage]
    _encrypt_confluence_tokens,  # pyright: ignore[reportPrivateUsage]
    _encrypt_http_headers,  # pyright: ignore[reportPrivateUsage]
    _encrypt_jira_tokens,  # pyright: ignore[reportPrivateUsage]
    _encrypt_vector_db_keys,  # pyright: ignore[reportPrivateUsage]
    _has_assignment,  # pyright: ignore[reportPrivateUsage]
    _to_workflow_read,  # pyright: ignore[reportPrivateUsage]
)
from src.builder.builder_agent import BuildSession, run_builder_turn
from src.config import get_settings
from src.engine.graph_builder import collect_workflow_violations
from src.engine.workflow import Workflow
from src.llm.providers import build_chat_model
from src.security.auth import get_current_role
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db

router = APIRouter(tags=["builder"])

DEFAULT_BUILDER_MODEL = "anthropic/claude-sonnet-4-5"
SESSION_TTL_SECONDS = 30 * 60


class BuilderMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: str | None = Field(default=None, alias="sessionId")
    execution_id: str | None = Field(default=None, alias="executionId")


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


async def _deployment_setting(db: Prisma, key: str) -> str | None:
    row = await db.deploymentsetting.find_unique(where={"key": key})  # pyright: ignore[reportAttributeAccessIssue]
    return row.value if row else None


@router.post("/builder/{workflow_id}/message", response_model=BuilderMessageResponse)
async def builder_message(  # pyright: ignore[reportUnusedFunction]
    workflow_id: str,
    payload: BuilderMessageRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
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

    model_string = (await _deployment_setting(db, "builder_model")) or DEFAULT_BUILDER_MODEL
    model = build_chat_model(model_string)

    workflow = _to_engine_workflow(existing)
    events, reply, updated = await run_builder_turn(
        model=model,
        workflow_id=workflow_id,
        workflow=workflow,
        session=session,
        message=payload.message,
        execution_id=payload.execution_id,
        db=db,
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
