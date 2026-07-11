"""Public, unauthenticated endpoint that resolves an emailed approve/reject link.

No login required by design — the signed token itself is the credential,
so external reviewers with no Composer account can decide. Single-use falls
out for free: resuming flips execution status away from 'waiting_approval'
immediately, so a second click on either link (or a stale one from an
earlier chained pause) naturally lands on the "no longer valid" redirect.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from starlette import status

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.security.jwt import TokenVerificationError, verify_approval_email_token
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
) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
    try:
        claims = verify_approval_email_token(token)
    except TokenVerificationError:
        return _redirect("invalid")

    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub}
    )
    if execution is None or execution.status != "waiting_approval":
        return _redirect("invalid")

    variables = execution.variables or {}
    pending_node_id = (
        variables.get("_pending_approval_node") if isinstance(variables, dict) else None
    )
    if pending_node_id != claims.node_id:
        return _redirect("invalid")

    await db.approval.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "executionId": claims.sub,
            "nodeId": claims.node_id,
            "approverUserId": None,
            "approverEmail": claims.approver_email,
            "viaEmailLink": True,
            "decision": claims.decision,
        }
    )

    await db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub},
        data={"status": "running"},
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
