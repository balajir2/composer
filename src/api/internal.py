"""Internal-only endpoints secured by Google-signed OIDC push auth (P1-2).

Not part of the public API surface; secured by OIDC token verification
(when CLOUD_TASKS_SERVICE_ACCOUNT is set) rather than user auth, since
the callers are Cloud Tasks and Cloud Scheduler, not end users:

  - POST /internal/claim-and-run — Cloud Tasks delivery target. Claims
    the execution via SELECT ... FOR UPDATE SKIP LOCKED (the pattern
    validated in scripts/poc_persistence_row_lock.py, ADR-0031) before
    running it — this, not Cloud Tasks' delivery guarantee, is what
    prevents two concurrent deliveries of the same task from
    double-running an execution.
  - POST /internal/sweep — Cloud Scheduler target (P1-2/P1-4). Runs all
    four maintenance sweeps (stuck executions, expired approvals,
    expired leases, old execution_events) once per invocation. See the
    2026-07-14 revision note in
    docs/superpowers/plans/2026-07-13-durable-execution-cloud-tasks.md's
    Task 11: this replaces relying on the in-process `_sweeper_loop`
    background task for lease recovery + retention cleanup, since that
    loop requires `--no-cpu-throttling` (removed as the dominant Cloud
    Run cost driver) to keep ticking between requests. A real inbound
    HTTP request needs no background CPU allocation.

Both endpoints share `_verify_internal_oidc`: Cloud Tasks and Cloud
Scheduler both authenticate push requests the same way (a Google-signed
OIDC token asserting a specific service-account identity), so one
verification function serves both rather than duplicating the logic.

Because these are real inbound HTTP requests (not BackgroundTasks
callbacks), Cloud Run keeps the instance alive for their full duration,
directly closing the scale-to-zero gap this subsystem exists to close.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, Field

from src.api.execution_status import ACTIVE_EXECUTION_STATUSES_SORTED
from src.config import get_settings
from src.engine.langgraph_executor import LangGraphExecutor
from src.engine.workflow import FileTriggerNode
from src.execution.cloud_tasks import enqueue_execution
from src.integrations.google_drive.oauth import get_valid_drive_access_token
from src.maintenance.execution_sweeper import (
    sweep_expired_approvals,
    sweep_expired_leases,
    sweep_old_execution_events,
    sweep_stuck_executions,
)
from src.storage.db import get_checkpointer, get_db, get_event_bus
from src.storage_providers.google_drive import GoogleDriveProvider
from src.storage_providers.text_extraction import extract_text

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


class ClaimAndRunRequest(BaseModel):
    # Field names follow this codebase's existing convention (see
    # src/api/run.py's RunRequest/RunAsyncResponse): snake_case in Python,
    # camelCase on the wire via alias, populate_by_name so either works.
    #
    # No `decision` field (P1-2 Task 13, supersedes the original draft):
    # enqueue_execution's Cloud Task body is hardcoded to
    # {"executionId": ..., "kind": ...} — there is no reliable way for a
    # request-body field to carry the approval decision through Cloud
    # Tasks' at-least-once, possibly-redelivered-later-by-a-sweeper
    # dispatch. The call sites that enqueue a "resume" (POST
    # /executions/{id}/resume, POST /approvals/email/{token}/confirm)
    # stamp `_resume_decision` into the execution row's `variables`
    # BEFORE enqueueing; `claim_and_run` reads it back from the row it
    # just claimed instead.
    execution_id: str = Field(alias="executionId")
    kind: Literal["run", "resume"] = "run"

    model_config = ConfigDict(populate_by_name=True)


def _verify_oidc_token_sync(token: str, audience: str) -> Mapping[str, Any]:
    """Blocking call — see docstring on `_verify_internal_oidc` for why
    this is wrapped in `asyncio.to_thread` at the call site."""
    return id_token.verify_oauth2_token(token, GoogleAuthRequest(), audience=audience)


async def _verify_internal_oidc(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """Verify the OIDC token Cloud Tasks/Cloud Scheduler presents.

    Shared by every endpoint in this router (claim-and-run, sweep) — Cloud
    Tasks and Cloud Scheduler both push via a Google-signed OIDC token
    asserting a service-account identity, so one verification function
    covers both rather than duplicating the logic per endpoint.

    Skipped entirely when CLOUD_TASKS_SERVICE_ACCOUNT is unset (local dev,
    or before the queue/scheduler is provisioned) — mirrors the existing
    dev-mode auth fallback pattern (src/main.py's ADR-0015 warning) rather
    than introducing a second, differently-shaped bypass.

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
    Tasks queue / Cloud Scheduler job is configured to use.

    The expected audience is derived from the current request's own path
    (`{backend_public_url}{request.url.path}`) rather than a single
    hardcoded endpoint, since each push target (claim-and-run, sweep) is
    provisioned with its own audience matching its own URL — Cloud Tasks'
    `enqueue_execution()` sets the OIDC token's audience to the task's
    target URL (src/execution/cloud_tasks.py), and a Cloud Scheduler HTTP
    target is configured the same way pointing at /internal/sweep.

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
    audience = f"{settings.backend_public_url}{request.url.path}"
    try:
        claims = await asyncio.to_thread(_verify_oidc_token_sync, token, audience)
    except (GoogleAuthError, ValueError) as exc:
        logger.warning("internal: OIDC verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid OIDC token"
        ) from exc

    if claims.get("email") != settings.cloud_tasks_service_account:
        logger.warning(
            "internal: OIDC token email %r does not match expected service account %r",
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
    _oidc: None = Depends(_verify_internal_oidc),
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
        # Status predicate is bound as a real parameter (Postgres
        # `= ANY($N::text[])`), not interpolated into the SQL text, and is
        # sourced from `ACTIVE_EXECUTION_STATUSES_SORTED`
        # (src/api/execution_status.py) rather than a hardcoded
        # `status IN (...)` literal — this was previously its own
        # independent hardcoded list, completely out of sync with the
        # identical set `executions.py`'s delete/cancel guards derive from
        # the same shared constant. A future change to the active-status
        # set now can't silently desync the claim query from those guards.
        rows = await tx.query_raw(
            """
            SELECT id FROM workflow_executions
            WHERE id = $1
              AND status = ANY($2::text[])
              AND (lease_expires_at IS NULL OR lease_expires_at < now())
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            payload.execution_id,
            ACTIVE_EXECUTION_STATUSES_SORTED,
        )
        if not rows:
            # Already claimed by another delivery, or the lease hasn't
            # expired yet — nothing to do. Returning 200 (not an error)
            # tells Cloud Tasks the delivery succeeded so it doesn't retry
            # a task that's genuinely already being handled.
            return {"status": "already_claimed"}

        # P1-2: the claim itself is what transitions a freshly-enqueued
        # `queued` row to `running` — start_execution (langgraph_executor.py)
        # deliberately creates rows as `queued` since Cloud Run can scale to
        # zero between "row created" and "Cloud Task delivered"; the row
        # should only claim to be `running` once a worker has genuinely
        # picked it up. Setting status unconditionally here is also
        # idempotent for the already-`running`/`waiting_approval` claim
        # cases (lease-recovery redelivery, resume) — sweep_expired_leases
        # only ever looks at `status='running'` rows with an expired lease
        # (src/maintenance/execution_sweeper.py), so this keeps that
        # invariant true from the moment of claim.
        # $2 is explicitly cast to `timestamptz` — Prisma Python's raw-query
        # binder tags a Python `datetime` parameter as `text` on the wire
        # (verified directly against real Postgres while writing this
        # code's integration test, tests/integration/test_durable_execution.py;
        # neither unit test mocks the DB so this never executed against
        # real Postgres before), and Postgres refuses to implicitly
        # assign a `text`-typed bind parameter to a `timestamp without
        # time zone` column ("column is of type timestamp without time
        # zone but expression is of type text"). Casting to `timestamptz`
        # first is safe regardless of session timezone: `lease_expires`
        # is always a timezone-aware UTC `datetime`, so the `timestamptz`
        # parse preserves the exact instant, and Postgres's implicit
        # assignment cast from `timestamptz` to `timestamp` then truncates
        # to the session's timezone (GMT on this project's Neon instances)
        # — the same UTC wall-clock value every other `leaseExpiresAt`
        # write in this codebase produces via the typed Prisma client
        # (e.g. sweep_expired_leases's `db.workflowexecution.update`).
        await tx.execute_raw(
            """
            UPDATE workflow_executions
            SET status = 'running', lease_owner = $1, lease_expires_at = $2::timestamptz,
                delivery_attempts = delivery_attempts + 1
            WHERE id = $3
            """,
            worker_id,
            lease_expires,
            payload.execution_id,
        )

    # By the time we reach this point, the lease UPDATE above has already
    # committed (the `async with db.tx(...)` block exited) — the row is
    # durably marked 'running' with an owner and an expiry BEFORE any of
    # the code below runs. That ordering matters for what follows: this
    # try/except is a fast-fail belt-and-suspenders layered ALONGSIDE
    # `sweep_expired_leases`, not a replacement for it (P1-2 Task 13
    # follow-up — restores the fast-fail path deleted in e50cb81 after
    # code review flagged that lease+sweep alone means up to
    # `execution_lease_seconds` (default 1h) before a crash is even
    # noticed, and up to `execution_max_delivery_attempts` sweep cycles
    # before dead-letter). If a genuine crash happens here (an uncaught
    # exception from the executor, a bug in this handler's own code), we
    # want the row marked 'failed' *now*, synchronously, in the same
    # request — not an hour from now.
    #
    # This mirrors the deleted `_run_with_persistence` wrapper
    # (src/api/run.py, removed in e50cb81) in spirit — the *DB* `error`
    # field keeps the exact `f"{type(exc).__name__}: {exc}"` format that
    # wrapper used — adapted from a detached-task done-callback to a
    # request handler: instead of re-raising for a task's done-callback to
    # observe, we raise an `HTTPException` so Cloud Tasks' HTTP client
    # sees a 5xx with a real body instead of an opaque, context-free 500.
    # The client-facing `detail` is deliberately generic, NOT the raw
    # exception text (P1-2 Task 13 follow-up, Issue 2) — that response
    # body is a different retention/IAM surface than this app's own
    # structured logs (Cloud Tasks receives and logs it), and a
    # DB-driver exception's `__str__` can include query/parameter
    # fragments with no functional need to appear there; the full detail
    # is already captured in `logger.exception` above and the DB `error`
    # field below. That 5xx may prompt Cloud Tasks to retry the
    # delivery, but that retry is a harmless no-op: the claim query above
    # only matches rows whose status is in `ACTIVE_EXECUTION_STATUSES_SORTED`,
    # and this code path has already stamped the row 'failed' before
    # raising, so the redelivered request finds nothing to claim and
    # returns `{"status": "already_claimed"}`.
    #
    # Deliberately `except Exception`, not `except BaseException`:
    # `asyncio.CancelledError` inherits from `BaseException` (not
    # `Exception`, since Python 3.8), so it is NOT caught here and
    # propagates untouched — a graceful Cloud Run scale-down/shutdown
    # cancelling this request must NOT get marked 'failed' by this fast
    # path; it correctly falls through to `sweep_expired_leases`'s slower
    # recovery instead, same as a hard worker kill that never runs any
    # Python code at all. `HTTPException` (itself an `Exception`
    # subclass) is caught and immediately re-raised unchanged, before the
    # generic handler below, so the existing "decision is required" 422
    # validation error is untouched by this guard — that's expected
    # application-level rejection, not a crash.
    #
    # The mark-failed write is guarded on `status = 'running'` (P1-2 Task
    # 13 follow-up, Issue 1) via `update_many`, matching the exact
    # pattern used everywhere else in this codebase a status transition
    # is written (`create_execution`'s enqueue-failure handler,
    # `cancel_execution`, `resume_execution` — all in src/api/executions.py):
    # fold the CURRENT status into the WHERE clause instead of an
    # unconditional `update()`. `cancel_execution`'s own docstring
    # confirms cancellation does not preempt an in-flight claim-and-run —
    # a still-running executor can crash into this handler *after* a
    # concurrent cancel has already flipped the row to 'canceled'.
    # Without the guard, this write would clobber that legitimate
    # terminal state with a confusing, incorrect 'failed'. If the guarded
    # write affects zero rows, something else already won the race and
    # that transition is authoritative — this handler reports success
    # rather than overwriting it or misreporting a crash.
    #
    # If persisting 'failed' itself fails (e.g. a DB error while writing
    # the failure), that inner exception is swallowed (logged, not
    # raised) so it can never mask the original crash — and the lease
    # committed above remains in place for `sweep_expired_leases` to
    # eventually recover. The safety net has its own safety net.
    try:
        checkpointer = get_checkpointer(request)
        event_bus = get_event_bus(request)
        executor = LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)

        if payload.kind == "resume":
            # P1-2 (Task 13): the decision travels via the claimed row's
            # `variables._resume_decision`, stamped by the enqueueing call
            # site BEFORE the Cloud Task fired (see ClaimAndRunRequest's
            # docstring) — not via any request-body field.
            row_data = await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
                where={"id": payload.execution_id}
            )
            decision = (row_data.variables or {}).get("_resume_decision") if row_data else None
            if not decision:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="decision is required when kind='resume'",
                )
            await executor.resume(payload.execution_id, decision)
        else:
            await executor.run(payload.execution_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "internal: claim_and_run crashed for execution %s (kind=%s)",
            payload.execution_id,
            payload.kind,
        )
        updated_count: int | None
        try:
            updated_count = await db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
                where={"id": payload.execution_id, "status": "running"},
                data={
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "completedAt": datetime.now(UTC),
                },
            )
        except Exception:
            logger.exception(
                "internal: failed to mark crashed execution %s as failed "
                "(lease remains for sweep_expired_leases to recover)",
                payload.execution_id,
            )
            updated_count = None

        if updated_count == 0:
            # Lost the race: something else (most likely a concurrent
            # cancel_execution) already transitioned this row out of
            # 'running' before this write landed. That transition is
            # legitimate and already correctly recorded — overwriting it
            # with 'failed' would be the exact bug this guard exists to
            # prevent, and re-raising the 500 below would misreport a
            # crash on a row whose outcome is already correct. Report
            # success instead, same spirit as the `already_claimed`
            # response above.
            logger.info(
                "internal: claim_and_run crash handler for execution %s found the "
                "row no longer 'running' — a concurrent transition already "
                "completed; not overwriting it",
                payload.execution_id,
            )
            return {"status": "already_terminal"}

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"execution {payload.execution_id} crashed during "
                f"{payload.kind}; see application logs for details"
            ),
        ) from exc

    return {"status": "completed"}


@router.post("/internal/sweep", status_code=status.HTTP_200_OK)
async def sweep(  # pyright: ignore[reportUnusedFunction]
    db: Any = Depends(get_db),
    _oidc: None = Depends(_verify_internal_oidc),
) -> dict[str, int | None]:
    """Cloud-Scheduler-triggered maintenance sweep (P1-2/P1-4).

    Runs all four sweep functions exactly once per invocation, reading
    thresholds from settings. Intentionally NOT wired into the
    in-process `_sweeper_loop` — see this module's docstring and the
    2026-07-14 revision note in Task 11 of
    docs/superpowers/plans/2026-07-13-durable-execution-cloud-tasks.md.

    Each of the four sweeps is isolated in its own try/except: this
    endpoint is now the SOLE production trigger for lease recovery and
    retention cleanup (no in-process background loop to fall back on —
    see the module docstring), so one sweep raising (e.g. a transient DB
    error on its initial query) must not prevent the other three from
    running, and must not turn the whole invocation into an unhandled
    500 that Cloud Scheduler just retries wholesale. A failed sweep's
    field is `null` in the response; every other field still reflects
    real work done this invocation.
    """
    settings = get_settings()

    stuck: int | None
    try:
        stuck_result = await sweep_stuck_executions(
            db, stuck_after_seconds=settings.execution_stuck_after_seconds
        )
        stuck = stuck_result.marked_failed
    except Exception:
        logger.exception("internal: sweep_stuck_executions failed")
        stuck = None

    approvals_expired: int | None
    try:
        approvals_result = await sweep_expired_approvals(
            db, timeout_hours=settings.approval_wait_timeout_hours
        )
        approvals_expired = approvals_result.marked_failed
    except Exception:
        logger.exception("internal: sweep_expired_approvals failed")
        approvals_expired = None

    leases_recovered: int | None
    try:
        leases_result = await sweep_expired_leases(
            db, max_delivery_attempts=settings.execution_max_delivery_attempts
        )
        # sweep_expired_leases's `marked_failed` counts dead-lettered rows;
        # `errored` counts rows whose recovery attempt raised (caught by
        # its own per-row try/except) — neither dead-lettered nor
        # genuinely recovered. Subtracting both from `scanned` is what
        # makes this an honest "actually recovered" count rather than
        # silently folding errored rows into a healthy-looking total.
        leases_recovered = (
            leases_result.scanned - leases_result.marked_failed - leases_result.errored
        )
    except Exception:
        logger.exception("internal: sweep_expired_leases failed")
        leases_recovered = None

    events_deleted: int | None
    try:
        events_deleted = await sweep_old_execution_events(
            db, retention_days=settings.execution_events_retention_days
        )
    except Exception:
        logger.exception("internal: sweep_old_execution_events failed")
        events_deleted = None

    return {
        "stuck": stuck,
        "approvals_expired": approvals_expired,
        "leases_recovered": leases_recovered,
        "events_deleted": events_deleted,
    }


@router.post("/internal/poll-file-triggers", status_code=status.HTTP_200_OK)
async def poll_file_triggers(  # pyright: ignore[reportUnusedFunction]
    request: Request,
    db: Any = Depends(get_db),
    _oidc: None = Depends(_verify_internal_oidc),
) -> dict[str, int]:
    """Cloud-Scheduler-triggered poll of every production workflow's
    google-drive file-trigger node — the server-side replacement for
    `composer watch`, which only works for locally-hosted folders. Fifth
    sweep-style endpoint alongside claim-and-run/sweep: same OIDC auth,
    same isolate-failures-per-item shape as `sweep`. See
    docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §D.

    Isolation is two-layered, both added in a post-implementation review
    (P1-2): a malformed `nodes` field on a workflow skips just that
    workflow (outer try/except below), and a malformed individual
    file-trigger node skips just that node (inner try/except) — neither
    aborts the rest of the poll, matching this endpoint's own
    isolate-failures-per-item contract.
    """
    settings = get_settings()
    workflows = await db.workflow.find_many(where={"isProduction": True})  # pyright: ignore[reportAttributeAccessIssue]
    checkpointer = get_checkpointer(request)
    event_bus = get_event_bus(request)
    executor = LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)

    triggered = 0
    failed = 0
    for wf in workflows:
        try:
            raw_nodes = wf.nodes or []
            if not isinstance(raw_nodes, list):
                raise TypeError(f"workflow.nodes is not a list (got {type(raw_nodes).__name__})")
        except Exception:
            logger.exception(
                "poll_file_triggers: workflow %s has a malformed nodes field; skipping", wf.id
            )
            continue

        for raw_node in raw_nodes:
            try:
                if raw_node.get("type") != "file-trigger":
                    continue
                data = raw_node.get("data", {})
                if data.get("provider") != "google-drive":
                    continue
                node = FileTriggerNode.model_validate(raw_node)
                connection_id = node.data.connection_id
                folder_id = node.data.drive_folder_id
                target_var = node.data.target_input_variable
                if not connection_id or not folder_id or not target_var:
                    continue
            except Exception:
                # A single malformed node (bad field types, missing
                # `data`, etc.) must not abort the rest of this
                # workflow's nodes, let alone every other workflow — the
                # same isolate-failures-per-item contract this endpoint
                # documents everywhere else. Prisma Python decodes `nodes`
                # Json as plain Python values, so a hand-edited/corrupted
                # entry can legitimately not be a dict here.
                node_id = raw_node.get("id") if isinstance(raw_node, dict) else raw_node
                logger.exception(
                    "poll_file_triggers: failed to parse file-trigger node %r in workflow %s; "
                    "skipping",
                    node_id,
                    wf.id,
                )
                continue

            connection = await db.cloudstorageconnection.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
                where={"id": connection_id}
            )
            if connection is None:
                logger.warning(
                    "poll_file_triggers: workflow %s node %s references missing connection %s",
                    wf.id,
                    node.id,
                    connection_id,
                )
                continue

            # Confused-deputy guard (P1-2 review, Critical #1): a
            # workflow's `nodes` JSON is owner-editable via the Workflow
            # CRUD API, and `connectionId` is just a plain cuid — nothing
            # ties it to that workflow's owner at write time. Without
            # this check, workflow owner A could point a file-trigger
            # node at a connection owned by user B, and this endpoint
            # would use B's OAuth token to list/read B's Drive files and
            # start an execution under B's identity. Same "private = 404
            # for non-owner" ownership convention this codebase applies
            # everywhere else (CLAUDE.md Phase 8) — applied here as
            # skip-not-raise since this endpoint isolates failures per
            # item rather than serving a single caller's request.
            # get_picker_token (src/api/cloud_storage_oauth.py) is the
            # 404-raising sibling of this same check for the interactive
            # (user-facing) path.
            if connection.userId != wf.userId:
                logger.warning(
                    "poll_file_triggers: workflow %s node %s references connection %s "
                    "owned by %s, not the workflow's owner %s — skipping "
                    "(confused-deputy guard)",
                    wf.id,
                    node.id,
                    connection_id,
                    connection.userId,
                    wf.userId,
                )
                continue

            try:
                access_token = await get_valid_drive_access_token(connection_id, db)
            except Exception:
                logger.exception(
                    "poll_file_triggers: failed to get access token for connection %s",
                    connection_id,
                )
                continue

            provider = GoogleDriveProvider(access_token)
            try:
                refs = await provider.list_new_files(folder_id)
            except Exception:
                logger.exception("poll_file_triggers: list_new_files failed for workflow %s", wf.id)
                continue

            for ref in refs:
                try:
                    raw = await provider.read_file(ref)
                    text = extract_text(ref.name, raw)

                    # Input-size cap (P1-2 review, Important #3): every
                    # other execution-creating entry point
                    # (src/api/run.py, src/api/executions.py) enforces
                    # settings.max_execution_input_bytes before calling
                    # start_execution — LangGraphExecutor.start_execution
                    # itself does not enforce it, that's a per-caller
                    # responsibility. A large extracted PDF/DOCX must not
                    # bypass that resource guard just because it arrived
                    # via this trigger instead of a direct API call.
                    # Measured the same way run.py does: true UTF-8 byte
                    # size of the JSON-encoded input, not len() of the
                    # raw text. Raising here (instead of a bespoke
                    # branch) reuses the exact per-file failure handling
                    # below — log, mark 'error', count it, move on.
                    input_payload = {target_var: text}
                    input_size = len(
                        _json.dumps(input_payload, default=str, ensure_ascii=False).encode("utf-8")
                    )
                    if input_size > settings.max_execution_input_bytes:
                        raise ValueError(
                            f"extracted text for file {ref.identifier!r} exceeds "
                            f"max_execution_input_bytes={settings.max_execution_input_bytes}; "
                            f"got {input_size}"
                        )

                    execution = await executor.start_execution(
                        workflow_id=wf.id,
                        input=input_payload,
                        user_id=connection.userId,
                    )
                    await enqueue_execution(execution.id, kind="run")
                except Exception:
                    logger.exception(
                        "poll_file_triggers: failed to process file %s (workflow %s)",
                        ref.identifier,
                        wf.id,
                    )
                    # Isolate a move_file failure the same way as the
                    # original processing failure: this is the error-path
                    # marker write, and the original exception (logged
                    # above) is what actually makes this a failed file —
                    # that's true whether or not marking it 'error' in
                    # Drive itself succeeds. If THIS also raises (Drive
                    # rejects the PATCH, transport error, etc.), log it too
                    # and fall through to `failed += 1` regardless, rather
                    # than letting a second exception propagate out of this
                    # request and abort every remaining workflow/file in
                    # the batch.
                    try:
                        await provider.move_file(ref, "error")
                    except Exception:
                        logger.exception(
                            "poll_file_triggers: failed to mark file %s as 'error' "
                            "(workflow %s) after the original processing failure above",
                            ref.identifier,
                            wf.id,
                        )
                    failed += 1
                    continue

                # Success-path marker write is isolated the same way: the
                # workflow execution already started successfully above,
                # so a move_file failure here must not crash the batch or
                # count as a processing failure. It also must not count as
                # `triggered` — the file stays unmarked in Drive and will
                # be picked up again (and re-executed) on the next poll
                # tick, an understood, narrow duplicate-execution tradeoff
                # already documented in the design doc's §G, not something
                # to solve here.
                try:
                    await provider.move_file(ref, "processed")
                except Exception:
                    logger.exception(
                        "poll_file_triggers: started execution %s for file %s (workflow %s) "
                        "but failed to mark it 'processed' — it may be reprocessed next poll",
                        execution.id,
                        ref.identifier,
                        wf.id,
                    )
                    continue
                triggered += 1

    return {"triggered": triggered, "failed": failed}


__all__ = ["router"]
