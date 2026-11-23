"""POST /builder/{workflowId}/message — the natural-language workflow builder."""

import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
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
from src.db.repos import workflow_assignments as assignments_repo
from src.db.repos import workflows as workflows_repo
from src.db.repos.workflows import WorkflowRecord
from src.db.runtime import require_sessionmaker
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


async def _load_workflow(
    sessionmaker: async_sessionmaker[AsyncSession], workflow_id: str, user_id: str, role: str
) -> WorkflowRecord:
    """Read the workflow and check access in one short session, closed on return.

    Callers hold no session afterwards, so the LLM loop never pins a connection.
    """
    async with sessionmaker() as session:
        return await _get_accessible(session, workflow_id, user_id, role)


async def _get_accessible(
    session: AsyncSession, workflow_id: str, user_id: str, role: str, *, for_update: bool = False
) -> WorkflowRecord:
    """The workflow if the caller may build on it: owner, admin or assignee.

    404 when it is gone; the same 404 as a missing row when access is lacking, so a
    workflow the caller may not see is not revealed to exist.
    """
    row = await workflows_repo.get(session, workflow_id, for_update=for_update)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id!r} not found.",
        )
    if (
        role != "admin"
        and row.user_id != user_id
        and not await assignments_repo.exists(session, workflow_id, user_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found.")
    return row


def _to_engine_workflow(row: WorkflowRecord) -> Workflow:
    return Workflow.model_validate(
        {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            # The JSONB columns are untyped: a migrated row could hold a non-list.
            "nodes": row.nodes if isinstance(row.nodes, list) else [],  # pyright: ignore[reportUnnecessaryIsInstance]
            "edges": row.edges if isinstance(row.edges, list) else [],  # pyright: ignore[reportUnnecessaryIsInstance]
        }
    )


async def _persist(
    sessionmaker: async_sessionmaker[AsyncSession],
    workflow_id: str,
    workflow: Workflow,
    existing: WorkflowRecord,
    user_id: str,
    role: str,
) -> WorkflowRecord:
    """Write nodes and edges in a fresh short session.

    Access is checked again under a row lock: it may have been revoked, or the
    workflow deleted, during the LLM turn. Nothing is committed in that case.

    Secrets are encrypted, and a redacted echo keeps the stored value, against the
    row read at load time (``existing``), as before the move to SQLAlchemy.
    """
    nodes_json = [node.model_dump(by_alias=True) for node in workflow.nodes]
    existing_nodes = existing.nodes
    _encrypt_jira_tokens(nodes_json, existing_nodes=existing_nodes)
    _encrypt_vector_db_keys(nodes_json, existing_nodes=existing_nodes)
    _encrypt_http_headers(nodes_json, existing_nodes=existing_nodes)
    _encrypt_confluence_tokens(nodes_json, existing_nodes=existing_nodes)
    edges_json = [edge.model_dump(by_alias=True) for edge in workflow.edges]
    # Metadata ruling: update ONLY nodes + edges; never the metadata columns.
    async with sessionmaker() as session:
        await _get_accessible(session, workflow_id, user_id, role, for_update=True)
        row = await workflows_repo.update(
            session, workflow_id, {"nodes": nodes_json, "edges": edges_json}
        )
        if row is None:  # deleted while the builder turn ran
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow {workflow_id!r} not found.",
            )
        await session.commit()
    return row


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

    sessionmaker = require_sessionmaker(request)
    existing = await _load_workflow(sessionmaker, workflow_id, user_id, role)

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
    row = await _persist(sessionmaker, workflow_id, updated, existing, user_id, role)
    validation_errors = collect_workflow_violations(updated)
    return BuilderMessageResponse(
        sessionId=sid,
        events=events,
        reply=reply,
        workflow=_to_workflow_read(row),
        validation_errors=validation_errors,
    )
