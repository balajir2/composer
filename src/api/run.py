"""POST /api/run/{slug} — external invoke of production workflows (Phase 10a).

Authenticates via 'Authorization: Bearer ck_<key>'.  Authz matches the
standard workflow read-authz policy: public workflows runnable by any
valid API key; private workflows only by the owner's key (or an admin's).
Supports async (default) and sync modes.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.engine.langgraph_executor import LangGraphExecutor
from src.execution.cloud_tasks import enqueue_execution
from src.security.api_key_auth import ApiKeyAuthResult, get_current_api_key_user
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db, get_event_bus

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
    from src.engine.events_pg import PostgresEventStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["run"])


class RunRequest(BaseModel):
    input: Any = None
    sync: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=300, alias="timeoutSeconds")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")

    model_config = ConfigDict(populate_by_name=True)


class RunAsyncResponse(BaseModel):
    executionId: str
    workflowId: str
    status: str
    streamUrl: str

    model_config = ConfigDict(populate_by_name=True)


class RunSyncResponse(BaseModel):
    executionId: str
    workflowId: str
    status: str
    output: Any = None

    model_config = ConfigDict(populate_by_name=True)


TERMINAL_STATUSES = {"completed", "failed", "canceled"}
# waiting_approval isn't terminal — the execution can still resume — but a
# sync caller polling for a result has no way to act on the pause, and
# polling all the way to timeout_seconds before reporting it (P1-6) makes
# a workflow paused for human review indistinguishable from one still
# running. Report it the moment it's observed, same as a real terminal state.
SYNC_EARLY_RETURN_STATUSES = TERMINAL_STATUSES | {"waiting_approval"}


def _get_executor(request: Request, db: Any, event_bus: Any) -> LangGraphExecutor:
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    return LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)


async def _find_execution_by_idempotency_key(
    db: Any, workflow_id: str, idempotency_key: str
) -> Any:
    where: dict[str, Any] = {
        "workflowId_idempotencyKey": {
            "workflowId": workflow_id,
            "idempotencyKey": idempotency_key,
        }
    }
    return await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where=where  # pyright: ignore[reportArgumentType]
    )


@router.post("/api/run/{slug}")
async def run_external(
    slug: str,
    payload: RunRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    event_bus: PostgresEventStore = Depends(get_event_bus),
    auth: ApiKeyAuthResult = Depends(get_current_api_key_user),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> Any:  # pyright: ignore[reportUnusedFunction]
    settings = get_settings()
    await enforce(
        limiter,
        route_key="api_run",
        client_key=auth.api_key_id,
        config=per_minute_config(settings.rate_limit_api_run_per_minute),
    )

    # Input size cap — reuse Phase 8's workflow_execution cap. Measure true
    # UTF-8 byte size, not the character length of json.dumps()'s default
    # ensure_ascii=True output — see the matching comment in
    # src/api/executions.py (P1-6).
    input_size = len(_json.dumps(payload.input, default=str, ensure_ascii=False).encode("utf-8"))
    if input_size > settings.max_execution_input_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"execution input exceeds max_bytes={settings.max_execution_input_bytes}; "
                f"got {input_size}"
            ),
        )

    # Look up by slug.  Non-production OR unknown = 404.
    workflow = await db.workflow.find_unique(where={"externalSlug": slug})  # pyright: ignore[reportAttributeAccessIssue]
    if workflow is None or not workflow.isProduction:
        raise HTTPException(status_code=404, detail=f"production workflow {slug!r} not found")

    # Authz: public OR owner-key OR admin.
    if not workflow.isPublic and workflow.userId != auth.user_id and auth.role != "admin":
        raise HTTPException(status_code=404, detail=f"production workflow {slug!r} not found")

    executor = _get_executor(request, db, event_bus)

    # Idempotent replay (P1-3): a caller retrying this POST after a network
    # timeout is the actual duplicate-execution vector here — LangGraph
    # doesn't auto-retry nodes, and the stuck-execution sweeper only marks
    # abandoned runs failed, it never re-runs them. A matching
    # (workflowId, idempotencyKey) row means an earlier request already
    # started this logical operation; reuse it instead of firing
    # side-effecting nodes (Jira, email, HTTP mutations) a second time.
    execution: Any = None
    is_replay = False
    if payload.idempotency_key:
        execution = await _find_execution_by_idempotency_key(
            db, workflow.id, payload.idempotency_key
        )
        is_replay = execution is not None

    if not is_replay:
        try:
            execution = await executor.start_execution(
                workflow_id=workflow.id,
                input=payload.input,
                user_id=auth.user_id,
                idempotency_key=payload.idempotency_key,
            )
        except UniqueViolationError:
            # Lost a race against a concurrent request carrying the same
            # key — the DB's unique constraint is the actual guarantee,
            # the find_unique lookup above is just a fast path.
            if payload.idempotency_key is None:
                raise
            execution = await _find_execution_by_idempotency_key(
                db, workflow.id, payload.idempotency_key
            )
            if execution is None:
                raise
            is_replay = True

    base_url = str(request.base_url).rstrip("/")
    stream_url = base_url.replace("http", "ws", 1) + f"/executions/{execution.id}/ws"

    if not is_replay:
        # P1-2 (Task 13): enqueue a Cloud Task instead of a request-bound
        # asyncio.create_task — Cloud Run can scale a request-bound
        # background task's instance to zero mid-run (ADR-0033). Cloud
        # Tasks' HTTP-push delivery to /internal/claim-and-run is a real
        # inbound request, which Cloud Run won't recycle mid-flight.
        # claim-and-run's lease + sweep_expired_leases (Task 11) is now
        # the crash-safety net for a worker dying mid-run — see this
        # module's removed `_run_with_persistence` in git history and the
        # Task 13 investigation notes in the durable-execution plan for
        # why that in-process wrapper became redundant once claim-and-run
        # (a real, lease-tracked inbound request) replaced the detached
        # asyncio task it used to wrap.
        await enqueue_execution(execution.id, kind="run")

    if not payload.sync:
        return RunAsyncResponse(
            executionId=execution.id,
            workflowId=workflow.id,
            status=execution.status,
            streamUrl=stream_url,
        )

    # Sync mode — poll until terminal, waiting_approval, or timeout_seconds.
    last_status = execution.status
    deadline = time.monotonic() + payload.timeout_seconds
    while time.monotonic() < deadline:
        current = await db.workflowexecution.find_unique(where={"id": execution.id})  # pyright: ignore[reportAttributeAccessIssue]
        if current is not None:
            last_status = current.status
            if current.status in SYNC_EARLY_RETURN_STATUSES:
                return RunSyncResponse(
                    executionId=execution.id,
                    workflowId=workflow.id,
                    status=current.status,
                    output=current.output,
                )
        await asyncio.sleep(0.25)

    # Timeout — fall back to async shape, reporting whatever status was
    # last observed rather than assuming 'running' (P1-6).
    return RunAsyncResponse(
        executionId=execution.id,
        workflowId=workflow.id,
        status=last_status,
        streamUrl=stream_url,
    )


__all__ = ["RunAsyncResponse", "RunRequest", "RunSyncResponse", "router"]
