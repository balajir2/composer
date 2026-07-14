"""POST /internal/claim-and-run — Cloud Tasks delivery target (P1-2).

Not part of the public API surface; secured by OIDC token verification
(when CLOUD_TASKS_SERVICE_ACCOUNT is set) rather than user auth, since
the caller is Cloud Tasks, not an end user. Claims the execution via
SELECT ... FOR UPDATE SKIP LOCKED (the pattern validated in
scripts/poc_persistence_row_lock.py, ADR-0031) before running it — this,
not Cloud Tasks' delivery guarantee, is what prevents two concurrent
deliveries of the same task from double-running an execution.

Because this is a real inbound HTTP request (not a BackgroundTasks
callback), Cloud Run keeps the instance alive for its full duration,
directly closing the scale-to-zero gap this endpoint exists to close.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, Field

from src.config import get_settings
from src.engine.langgraph_executor import LangGraphExecutor
from src.storage.db import get_checkpointer, get_db, get_event_bus

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


class ClaimAndRunRequest(BaseModel):
    # Field names follow this codebase's existing convention (see
    # src/api/run.py's RunRequest/RunAsyncResponse): snake_case in Python,
    # camelCase on the wire via alias, populate_by_name so either works.
    execution_id: str = Field(alias="executionId")
    kind: Literal["run", "resume"] = "run"
    decision: str | None = None  # required when kind == "resume"

    model_config = ConfigDict(populate_by_name=True)


def _verify_oidc_token_sync(token: str, audience: str) -> Mapping[str, Any]:
    """Blocking call — see docstring on `_verify_cloud_tasks_oidc` for why
    this is wrapped in `asyncio.to_thread` at the call site."""
    return id_token.verify_oauth2_token(token, GoogleAuthRequest(), audience=audience)


async def _verify_cloud_tasks_oidc(authorization: str | None = Header(default=None)) -> None:
    """Verify the OIDC token Cloud Tasks/Cloud Scheduler presents.

    Skipped entirely when CLOUD_TASKS_SERVICE_ACCOUNT is unset (local dev,
    or before the queue is provisioned) — mirrors the existing dev-mode
    auth fallback pattern (src/main.py's ADR-0015 warning) rather than
    introducing a second, differently-shaped bypass.

    Uses google-auth's verify_oauth2_token (JWKS fetch/cache, signature,
    issuer, expiry all handled internally) rather than hand-rolling JWT
    verification — this is Google's own recommended pattern for verifying
    Cloud Tasks/Cloud Scheduler OIDC push-auth tokens.

    Two checks, not one: (1) the token must be a validly-signed, unexpired
    Google-issued OIDC token for the expected audience (verify_oauth2_token
    itself), and (2) the token's `email` claim — the identity Google's
    token asserts — must match settings.cloud_tasks_service_account
    exactly. Check (1) alone is not an authorization check: any Google
    service account (anyone's) can mint a validly-signed token; only (2)
    ties the request to the specific service account this app's Cloud
    Tasks queue is configured to use.

    `verify_oauth2_token` is synchronous and may do a network round-trip
    to fetch/cache Google's JWKS on first use (subsequent calls hit the
    in-process cache google-auth keeps internally, so steady-state cost is
    low) — run it via `asyncio.to_thread` so a cold-cache fetch doesn't
    block the event loop for every other in-flight request on this worker.
    """
    settings = get_settings()
    if not settings.cloud_tasks_service_account:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing OIDC token")
    token = authorization.removeprefix("Bearer ")
    # Must match exactly what src/execution/cloud_tasks.py's
    # enqueue_execution() sets as the OIDC token's audience.
    audience = f"{settings.backend_public_url}/internal/claim-and-run"
    try:
        claims = await asyncio.to_thread(_verify_oidc_token_sync, token, audience)
    except (GoogleAuthError, ValueError) as exc:
        logger.warning("internal.claim_and_run: OIDC verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid OIDC token"
        ) from exc

    if claims.get("email") != settings.cloud_tasks_service_account:
        logger.warning(
            "internal.claim_and_run: OIDC token email %r does not match expected "
            "service account %r",
            claims.get("email"),
            settings.cloud_tasks_service_account,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unexpected token issuer"
        )


@router.post("/internal/claim-and-run", status_code=status.HTTP_200_OK)
async def claim_and_run(  # pyright: ignore[reportUnusedFunction]
    payload: ClaimAndRunRequest,
    request: Request,
    db: Any = Depends(get_db),
    _oidc: None = Depends(_verify_cloud_tasks_oidc),
) -> dict[str, str]:
    lease_seconds = get_settings().execution_lease_seconds
    now = datetime.now(UTC)
    lease_expires = now + timedelta(seconds=lease_seconds)
    worker_id = f"{request.client.host if request.client else 'unknown'}:{id(request)}"

    # Fixed, short timeout for the claim transaction — intentionally NOT
    # scaled to `execution_lease_seconds`. The lease duration sizes the
    # REQUEST's overall run time (the lease *is* the heartbeat; see
    # src/config.py's design-decision comment on execution_lease_seconds),
    # not this transaction, which does only one SELECT + one UPDATE and
    # commits before `executor.run()`/`.resume()` is ever called. Sizing it
    # to the lease (up to 1h by default) would mean an anomalous hang here
    # holds the row lock and a pool connection for up to an hour instead of
    # failing fast — blocking unrelated operations (cancel, delete, status
    # reads) on that row. Matches the precedent set by the identical
    # FOR UPDATE SKIP LOCKED pattern in scripts/poc_persistence_row_lock.py.
    async with db.tx(timeout=10000) as tx:
        rows = await tx.query_raw(
            """
            SELECT id FROM workflow_executions
            WHERE id = $1
              AND status IN ('running', 'waiting_approval')
              AND (lease_expires_at IS NULL OR lease_expires_at < now())
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            payload.execution_id,
        )
        if not rows:
            # Already claimed by another delivery, or the lease hasn't
            # expired yet — nothing to do. Returning 200 (not an error)
            # tells Cloud Tasks the delivery succeeded so it doesn't retry
            # a task that's genuinely already being handled.
            return {"status": "already_claimed"}

        await tx.execute_raw(
            """
            UPDATE workflow_executions
            SET lease_owner = $1, lease_expires_at = $2,
                delivery_attempts = delivery_attempts + 1
            WHERE id = $3
            """,
            worker_id,
            lease_expires,
            payload.execution_id,
        )

    checkpointer = get_checkpointer(request)
    event_bus = get_event_bus(request)
    executor = LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)

    if payload.kind == "resume":
        if not payload.decision:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="decision is required when kind='resume'",
            )
        await executor.resume(payload.execution_id, payload.decision)
    else:
        await executor.run(payload.execution_id)

    return {"status": "completed"}


__all__ = ["router"]
