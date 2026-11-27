"""Public, unauthenticated endpoints that resolve an emailed approve/reject link.

No login required by design — the signed token itself is the credential,
so external reviewers with no Composer account can decide.

Split into two steps (P1-1): GET only validates the token and redirects
to a confirmation page — it never mutates state. Email security scanners
and preview services routinely follow GET links automatically to check
for malware/phishing, and a state-changing GET would let the first
scanner-opened link silently make the decision before a human ever sees
it. Only the POST /confirm step (triggered by a real user clicking
"Confirm" on the frontend confirmation page) performs the mutation.

Single-use is enforced by one guarded `UPDATE` that also checks the pause
instance (`executions_repo.resume_if_waiting`) rather than a separate
check-then-act pair, which would otherwise let two near-simultaneous
requests (a duplicate click, or two reviewers racing) both pass the check
before either commits. The approval audit row commits in the same
transaction; the Cloud Task is enqueued only after the session closes.
"""

from typing import Any, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette import status

from src.api._runnable import refuse_unrunnable_row
from src.config import get_settings
from src.db.models import ApprovalDecision
from src.db.repos import approvals as approvals_repo
from src.db.repos import executions as executions_repo
from src.db.repos import workflows as workflows_repo
from src.db.repos.executions import ExecutionRecord, PendingInstance
from src.db.runtime import require_sessionmaker
from src.db.types import utcnow
from src.execution.cloud_tasks import enqueue_execution
from src.security.jwt import (
    ApprovalEmailTokenPayload,
    TokenVerificationError,
    verify_approval_email_token,
)
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)

router = APIRouter(tags=["approvals"])


def _redirect_result(status_param: str) -> RedirectResponse:
    settings = get_settings()
    params = urlencode({"status": status_param})
    return RedirectResponse(
        url=f"{settings.frontend_url}/approval-result?{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _redirect_confirm(token: str, decision: str) -> RedirectResponse:
    settings = get_settings()
    params = urlencode({"token": token, "decision": decision})
    return RedirectResponse(
        url=f"{settings.frontend_url}/approval-confirm?{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _verify(token: str) -> ApprovalEmailTokenPayload | None:
    try:
        return verify_approval_email_token(token)
    except TokenVerificationError:
        return None


def _matches_pause(execution: ExecutionRecord | None, claims: ApprovalEmailTokenPayload) -> bool:
    """Read-only check shared by GET and POST: the row is still paused at the token's instance.

    Binds the token to the specific pause *instance*, not just the node: a `while`-loop
    user-approval node can pause repeatedly at the same node_id, so a node_id-only check would
    let a stale token from an earlier iteration resolve a later one.
    """
    if execution is None or execution.status != "waiting_approval":
        return False
    variables = execution.variables
    if not isinstance(variables, dict):
        return False
    pause = cast("dict[str, Any]", variables)
    return (
        pause.get("_pending_approval_node") == claims.node_id
        and pause.get("_pending_approval_since") == claims.pending_since
    )


@router.get("/approvals/email/{token}")
async def resolve_approval_email(
    token: str,
    request: Request,
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
    """Read-only: validates the token and redirects to the confirmation
    page. Never mutates state, so a scanner/preview service prefetching
    this link is harmless — see module docstring."""
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="approval_email",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_approval_email_per_minute),
    )
    sessionmaker = require_sessionmaker(request)
    claims = _verify(token)
    if claims is None:
        return _redirect_result("invalid")
    async with sessionmaker() as session:
        execution = await executions_repo.get(session, claims.sub)
    if not _matches_pause(execution, claims):
        return _redirect_result("invalid")
    return _redirect_confirm(token, claims.decision)


@router.post("/approvals/email/{token}/confirm")
async def confirm_approval_email(
    token: str,
    request: Request,
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
    """The actual mutation — only reached via a deliberate POST (the
    frontend confirmation page's form submission), never a bare GET.
    Re-validates everything from scratch rather than trusting the GET
    that (probably) preceded it, since state may have changed in
    between (e.g. resolved through another channel, or simply expired).
    No CSRF token needed beyond the signed token itself: this endpoint
    has no ambient session/cookie to ride, so a cross-site form couldn't
    submit a valid request without already knowing the token — the same
    credential the GET link itself required.
    """
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="approval_email",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_approval_email_per_minute),
    )
    sessionmaker = require_sessionmaker(request)
    claims = _verify(token)
    if claims is None:
        return _redirect_result("invalid")
    try:
        decision = ApprovalDecision(claims.decision)  # DB6: unknown claim -> invalid, no write
    except ValueError:
        return _redirect_result("invalid")
    async with sessionmaker() as session:
        execution = await executions_repo.get(session, claims.sub)
        if not _matches_pause(execution, claims) or execution is None:
            return _redirect_result("invalid")
        # Edited while paused into a workflow that cannot run: leave the pause (and the
        # link) intact instead of recording a decision that would only fail the run.
        workflow_row = await workflows_repo.get(session, execution.workflow_id)
        if workflow_row is not None:
            try:
                refuse_unrunnable_row(workflow_row)
            except HTTPException:
                return _redirect_result("unrunnable")
        # The approval row and guarded transition share a transaction, so a losing transition
        # rolls the approval insert back. Its ID is carried to the later resume claim.
        approval = await approvals_repo.create(
            session,
            execution_id=claims.sub,
            node_id=claims.node_id,
            decision=decision,
            approver_user_id=None,
            approver_email=claims.approver_email,
            via_email_link=True,
            note=None,
        )
        won = await executions_repo.resume_if_waiting(
            session,
            claims.sub,
            decision=decision.value,
            now=utcnow(),
            expected_pending=PendingInstance(node_id=claims.node_id, since=claims.pending_since),
            approval_id=approval.id,
        )
        if not won:
            return _redirect_result("invalid")
        await session.commit()
    await enqueue_execution(claims.sub, kind="resume")
    return _redirect_result(decision.value)


__all__ = ["router"]
