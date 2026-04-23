"""POST /api/run/{slug} — external invoke of production workflows (Phase 10a).

Authenticates via 'Authorization: Bearer ck_<key>'.  Authz matches the
standard workflow read-authz policy: public workflows runnable by any
valid API key; private workflows only by the owner's key (or an admin's).
Supports async (default) and sync modes.
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.engine.langgraph_executor import LangGraphExecutor
from src.security.api_key_auth import ApiKeyAuthResult, get_current_api_key_user
from src.security.rate_limit import (
    RateLimiter,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db, get_event_bus

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
    from src.engine.events import ExecutionEventBus

router = APIRouter(tags=["run"])


class RunRequest(BaseModel):
    input: Any = None
    sync: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=300, alias="timeoutSeconds")

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


def _get_executor(request: Request, db: Any, event_bus: Any) -> LangGraphExecutor:
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    return LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)


@router.post("/api/run/{slug}")
async def run_external(
    slug: str,
    payload: RunRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    event_bus: ExecutionEventBus = Depends(get_event_bus),
    auth: ApiKeyAuthResult = Depends(get_current_api_key_user),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> Any:  # pyright: ignore[reportUnusedFunction]
    settings = get_settings()
    await enforce(
        limiter,
        route_key="api_run",
        client_key=auth.api_key_id,
        config=per_minute_config(settings.rate_limit_api_run_per_minute),
    )

    # Input size cap — reuse Phase 8's workflow_execution cap.
    input_size = len(_json.dumps(payload.input, default=str))
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

    # Start execution via the same executor users hit through POST /executions.
    executor = _get_executor(request, db, event_bus)
    execution = await executor.start_execution(
        workflow_id=workflow.id, input=payload.input, user_id=auth.user_id
    )

    base_url = str(request.base_url).rstrip("/")
    stream_url = base_url.replace("http", "ws", 1) + f"/executions/{execution.id}/ws"

    # Fire-and-forget run task.  Store reference to avoid RUF006.
    _task = asyncio.create_task(executor.run(execution.id))
    # Suppress "task was destroyed but it is pending" on test teardown.
    _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    if not payload.sync:
        return RunAsyncResponse(
            executionId=execution.id,
            workflowId=workflow.id,
            status=execution.status,
            streamUrl=stream_url,
        )

    # Sync mode — poll until terminal or timeout_seconds.
    deadline = time.monotonic() + payload.timeout_seconds
    while time.monotonic() < deadline:
        current = await db.workflowexecution.find_unique(where={"id": execution.id})  # pyright: ignore[reportAttributeAccessIssue]
        if current is not None and current.status in TERMINAL_STATUSES:
            return RunSyncResponse(
                executionId=execution.id,
                workflowId=workflow.id,
                status=current.status,
                output=current.output,
            )
        await asyncio.sleep(0.25)

    # Timeout — fall back to async shape.
    return RunAsyncResponse(
        executionId=execution.id,
        workflowId=workflow.id,
        status="running",
        streamUrl=stream_url,
    )


__all__ = ["RunAsyncResponse", "RunRequest", "RunSyncResponse", "router"]
