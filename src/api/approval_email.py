"""Public, unauthenticated endpoint that resolves an emailed approve/reject link.

No login required by design — the signed token itself is the credential,
so external reviewers with no Composer account can decide. Single-use is
enforced explicitly via an atomic conditional `update_many` (see
`resolve_approval_email` below) rather than relying on a separate
check-then-act pair, which would otherwise let two near-simultaneous
requests (an email-security scanner prefetching both the Approve and
Reject links, or a duplicate browser retry) both pass the check before
either commits.
"""

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from starlette import status

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.security.jwt import TokenVerificationError, verify_approval_email_token
from src.security.rate_limit import RateLimiter, enforce, get_rate_limiter, per_minute_config
from src.storage.db import get_db

router = APIRouter(tags=["approvals"])


def _redirect(status_param: str) -> RedirectResponse:
    settings = get_settings()
    params = urlencode({"status": status_param})
    return RedirectResponse(
        url=f"{settings.frontend_url}/approval-result?{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/approvals/email/{token}")
async def resolve_approval_email(
    token: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="approval_email",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_approval_email_per_minute),
    )

    try:
        claims = verify_approval_email_token(token)
    except TokenVerificationError:
        return _redirect("invalid")

    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub}
    )
    if execution is None:
        return _redirect("invalid")

    # Bind the token to the specific pause *instance*, not just the node --
    # a `while`-loop user-approval node can pause repeatedly at the same
    # node_id, so a node_id-only check would let a stale token from an
    # earlier iteration resolve a later one.
    variables = execution.variables or {}
    pending_node_id = (
        variables.get("_pending_approval_node") if isinstance(variables, dict) else None
    )
    pending_since = (
        variables.get("_pending_approval_since") if isinstance(variables, dict) else None
    )
    if pending_node_id != claims.node_id or pending_since != claims.pending_since:
        return _redirect("invalid")

    # Atomic conditional status transition -- closes the check-then-act race
    # a separate find_unique + update pair would leave open. update_many (not
    # update, which requires a unique-key where) lets us fold the guard
    # (status must still be waiting_approval) into the same statement that
    # performs the transition, so at most one of two near-simultaneous
    # requests for the same pause can ever flip this row. Prisma Python's
    # update_many returns the affected-row count directly as an int (see
    # src/migration/reconcile.py for the same convention), not a BatchPayload
    # wrapper object.
    updated_count = await db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub, "status": "waiting_approval"},
        data={"status": "running"},
    )
    if updated_count != 1:
        return _redirect("invalid")

    approval_data: dict[str, Any] = {
        "executionId": claims.sub,
        "nodeId": claims.node_id,
        "approverUserId": None,
        "approverEmail": claims.approver_email,
        "viaEmailLink": True,
        "decision": claims.decision,
    }
    await db.approval.create(  # pyright: ignore[reportAttributeAccessIssue]
        data=approval_data,  # pyright: ignore[reportArgumentType]
    )

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Checkpointer not attached to app.state")
    event_bus = getattr(request.app.state, "event_bus", None)
    from src.engine.langgraph_executor import LangGraphExecutor

    executor = LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)
    background_tasks.add_task(executor.resume, claims.sub, claims.decision)

    return _redirect(claims.decision)


__all__ = ["router"]
