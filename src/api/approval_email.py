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

Single-use is enforced explicitly via an atomic conditional `update_many`
in the POST handler (see `confirm_approval_email` below) rather than a
separate check-then-act pair, which would otherwise let two
near-simultaneous requests (a duplicate click, or two reviewers racing)
both pass the check before either commits.
"""

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from starlette import status

from prisma import Json, Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
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
from src.storage.db import get_db

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


async def _validate_token_and_execution(
    token: str,
    db: Prisma,  # pyright: ignore[reportUnknownParameterType]
) -> tuple[ApprovalEmailTokenPayload, Any] | None:
    """Shared, read-only validation for both GET and POST. Returns
    (claims, execution_row) on success, None on any failure — never
    mutates anything."""
    try:
        claims = verify_approval_email_token(token)
    except TokenVerificationError:
        return None

    execution = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub}
    )
    if execution is None:
        return None
    if execution.status != "waiting_approval":
        return None

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
        return None

    return claims, execution


@router.get("/approvals/email/{token}")
async def resolve_approval_email(
    token: str,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
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

    result = await _validate_token_and_execution(token, db)
    if result is None:
        return _redirect_result("invalid")
    claims, _execution = result
    return _redirect_confirm(token, claims.decision)


@router.post("/approvals/email/{token}/confirm")
async def confirm_approval_email(
    token: str,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
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

    result = await _validate_token_and_execution(token, db)
    if result is None:
        return _redirect_result("invalid")
    claims, execution = result

    # Atomic conditional status transition -- closes the check-then-act race
    # a separate find_unique + update pair would leave open. update_many (not
    # update, which requires a unique-key where) lets us fold the guard
    # (status must still be waiting_approval) into the same statement that
    # performs the transition, so at most one of two near-simultaneous
    # requests for the same pause can ever flip this row. Prisma Python's
    # update_many returns the affected-row count directly as an int (see
    # src/migration/reconcile.py for the same convention), not a BatchPayload
    # wrapper object.
    # Also clears the lease from the original claim: without this, the
    # stale (still-unexpired) lease from when this execution was first
    # claimed by POST /internal/claim-and-run blocks the fresh Cloud Task
    # delivery (enqueued below) from re-claiming the row — claim_and_run's
    # claim query requires `lease_expires_at IS NULL OR lease_expires_at <
    # now()`, and the original claim's lease (default
    # execution_lease_seconds=3600s) is typically still unexpired at
    # resume time, since most approvals resolve well within an hour. Left
    # unfixed, the row would silently stall at status='running' — the
    # fresh delivery finds zero claimable rows and returns
    # {"status": "already_claimed"} — until sweep_expired_leases notices
    # the stale lease has expired (up to execution_lease_seconds later)
    # and self-heals it. See 2026-07-15 holistic branch-wide review
    # finding (P1-2 fast-follow): reproduced end-to-end against real
    # Postgres, invisible to mocked-DB unit tests since they never
    # enforce the real claim-query predicate against a genuinely-set
    # lease.
    updated_count = await db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub, "status": "waiting_approval"},
        data={"status": "running", "leaseOwner": None, "leaseExpiresAt": None},
    )
    if updated_count != 1:
        return _redirect_result("invalid")

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

    # P1-2 (Task 13): stamp the decision into `variables` BEFORE enqueueing
    # the Cloud Task — same pattern as POST /executions/{id}/resume
    # (src/api/executions.py). enqueue_execution's Cloud Task body is
    # hardcoded to {"executionId": ..., "kind": ...}, so the decision
    # travels as row state that claim-and-run reads back after claiming,
    # not as a Cloud Task payload field.
    await db.workflowexecution.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": claims.sub},
        data={
            "variables": Json({**(execution.variables or {}), "_resume_decision": claims.decision})
        },
    )
    await enqueue_execution(claims.sub, kind="resume")

    return _redirect_result(claims.decision)


__all__ = ["router"]
