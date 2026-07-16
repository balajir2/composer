"""Stuck-execution sweeper.

A `WorkflowExecution` row is created with `status='queued'` the moment the
API accepts a request (P1-2: `LangGraphExecutor.start_execution`) — the
request handler enqueues a Cloud Task and returns immediately; it does NOT
await the run. The row only flips to `running` once an independent,
Cloud-Tasks-pushed request to `POST /internal/claim-and-run` actually claims
it, and from there to `completed`/`failed`/`waiting_approval` once
`LangGraphExecutor.run()`/`.resume()` finishes. So the row can legitimately
linger in `queued` for a short window even under normal conditions (the gap
between row creation and Cloud Tasks delivering the claim request) — that
window is not, by itself, evidence of a stuck row.

Three real-world failure modes still leave rows stuck in `running`:

  1. The worker process is killed mid-run (uvicorn reload, SIGKILL, OOM).
  2. The serverless runtime hits its function-duration ceiling
     (Vercel ~5 min, AWS Lambda ~15 min) and terminates the process.
  3. A bug somewhere in the executor chain skips the persist call entirely.

Without a sweeper, those rows say `running` forever — polluting dashboards,
hiding token spend, and making it impossible to tell "did it finish?" from
the DB row alone.

This module provides:

  - `sweep_stuck_executions(db, ...)` — a one-shot function tests can call
    directly to verify the policy.  Returns a `SweepResult` so callers can
    log how many rows it touched.
  - `sweep_expired_approvals(db, ...)` — the equivalent one-shot function
    for `waiting_approval` rows.  See its docstring for why it's a separate
    function with a separate, much longer timeout.
  - `sweep_expired_leases(db, ...)` — recovers `running` rows whose
    Cloud-Tasks-claim lease (`leaseExpiresAt`, P1-2) expired without the
    worker completing: clears the lease and re-enqueues a fresh Cloud
    Task (or dead-letters past `max_delivery_attempts`). See its own
    docstring for why this does NOT make `sweep_stuck_executions`
    redundant — the two are complementary, not overlapping. Not wired
    into `_sweeper_loop`; called from `POST /internal/sweep` instead
    (see src/api/internal.py) — the 2026-07-14 revision that dropped
    `--no-cpu-throttling` means nothing may rely on in-process background
    ticking between requests any more.
  - `sweep_old_execution_events(db, ...)` — pure hygiene: deletes
    `execution_events` rows past `execution_events_retention_days`
    (P1-4). Also called only from `POST /internal/sweep`, not the
    in-process loop.
  - `start_sweeper(app, ...)` — schedules `sweep_stuck_executions` and
    `sweep_expired_approvals` on a recurring background task tied to the
    FastAPI lifespan.  Idempotent if the interval is 0 (sweeper
    disabled) — useful for local/dev convenience or any environment that
    prefers an external cron; production relies on `POST /internal/sweep`
    + Cloud Scheduler for all four sweeps instead (interval <= 0 in
    production).

`sweep_stuck_executions` only touches rows in `status='running'`.
`waiting_approval` rows are excluded from it — they're paused on purpose
and may sit for days waiting for a human reviewer — but they are not
unbounded: `sweep_expired_approvals` reaps them on its own, longer timeout.

**Relationship between `sweep_stuck_executions` and `sweep_expired_leases`
(P1-2 addition):** both ultimately target `status='running'` rows past a
deadline, but they are deliberately kept as two separate functions rather
than merged, because they cover different failure shapes:

  - `sweep_expired_leases` is the precise, primary recovery path: it only
    matches rows with a non-NULL `leaseExpiresAt` that has actually
    passed — i.e. rows that WERE claimed by claim-and-run and then the
    worker died. It knows how to *recover* (re-enqueue) as well as
    dead-letter, and preserves run-vs-resume kind.
  - `sweep_stuck_executions` is a coarser, second safety net: its
    `startedAt`-based timeout also catches rows that somehow never got a
    lease at all (e.g. a crash between `WorkflowExecution.create()` and
    claim-and-run's lease UPDATE, or any future code path that flips a
    row to `running` without going through claim-and-run). Those rows
    have `leaseExpiresAt IS NULL`, which `sweep_expired_leases`'s
    `{"lt": now}` filter never matches (SQL `NULL < x` is `NULL`, not
    `true`) — so without `sweep_stuck_executions` they would say
    `running` forever. It only marks failed (no re-enqueue), which is
    the right, conservative behavior for a row whose provenance is
    already uncertain.

Both stay in the codebase; neither was deleted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    """Outcome of a single sweeper run, for logging + tests.

    `errored` (default 0, added alongside the P1-2/P1-4 code-review fix
    that reordered `sweep_expired_leases`'s enqueue-before-clear) counts
    rows whose recovery attempt raised and was caught by the per-row
    try/except — neither dead-lettered nor genuinely recovered. Only
    `sweep_expired_leases` currently populates it with anything other
    than 0; `sweep_stuck_executions` and `sweep_expired_approvals` don't
    have a third outcome to track, so their callers/tests (which
    construct `SweepResult(scanned=..., marked_failed=...)` without it)
    are unaffected by the default.
    """

    scanned: int
    marked_failed: int
    errored: int = 0


_TIMEOUT_ERROR_MESSAGE_TEMPLATE = (
    "Execution exceeded {seconds}s without a terminal status update. The "
    "worker likely died mid-run (process killed, runtime timeout, or "
    "uncaught crash). Marked failed by the stuck-execution sweeper."
)


async def sweep_stuck_executions(
    db: Any,
    *,
    stuck_after_seconds: int,
    now: datetime | None = None,
) -> SweepResult:
    """Find `running` executions older than `stuck_after_seconds` and fail them.

    Args:
        db: Prisma client (duck-typed so tests can pass a stub).
        stuck_after_seconds: how long a row may stay 'running' before being
            considered abandoned.  Must be > 0; lower bound is enforced.
        now: optional time override for tests.

    Returns:
        `SweepResult` with the number of rows scanned and the number marked
        failed.  Always returns — exceptions on individual rows are caught
        and logged, never re-raised, because the sweeper runs in a
        background loop where one bad row should not stop future sweeps.
    """
    if stuck_after_seconds <= 0:
        raise ValueError("stuck_after_seconds must be > 0")

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(seconds=stuck_after_seconds)

    # Filter on `startedAt` since `running` rows always have one (set by
    # WorkflowExecution.create() default).  The default Prisma cmp is `<` —
    # rows whose startedAt is older than the cutoff are stale.
    rows: list[Any] = await db.workflowexecution.find_many(
        where={
            "status": "running",
            "startedAt": {"lt": cutoff},
        },
    )

    marked = 0
    for row in rows:
        try:
            await db.workflowexecution.update(
                where={"id": row.id},
                data={
                    "status": "failed",
                    "error": _TIMEOUT_ERROR_MESSAGE_TEMPLATE.format(
                        seconds=stuck_after_seconds,
                    ),
                    "completedAt": current_time,
                },
            )
            marked += 1
        except Exception:
            logger.exception(
                "execution_sweeper: failed to mark execution %s as failed",
                getattr(row, "id", "<unknown>"),
            )

    if marked > 0:
        logger.info(
            "execution_sweeper: marked %d/%d stuck executions as failed",
            marked,
            len(rows),
        )

    return SweepResult(scanned=len(rows), marked_failed=marked)


_APPROVAL_TIMEOUT_ERROR_TEMPLATE = (
    "Execution expired waiting for approval after {hours}h with no decision. "
    "Auto-failed by the approval-timeout sweeper."
)


async def sweep_expired_approvals(
    db: Any,
    *,
    timeout_hours: int,
    now: datetime | None = None,
) -> SweepResult:
    """Find waiting_approval executions past timeout_hours since they entered
    that state, and fail them.

    Independent of sweep_stuck_executions — waiting_approval rows are
    deliberately excluded from that function (see its docstring); they get
    their own, much longer timeout here instead, since this is a
    Postgres/checkpoint-row-growth hygiene measure, not a crash-recovery one
    (a paused execution holds no compute — see
    docs/archive/phase-history/specs/2026-07-11-approval-email-notifications-design.md).
    """
    if timeout_hours <= 0:
        raise ValueError("timeout_hours must be > 0")

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(hours=timeout_hours)

    rows: list[Any] = await db.workflowexecution.find_many(
        where={"status": "waiting_approval"},
    )

    marked = 0
    for row in rows:
        variables = getattr(row, "variables", None) or {}
        since_raw = (
            variables.get("_pending_approval_since") if isinstance(variables, dict) else None
        )
        if not since_raw:
            continue
        try:
            since = datetime.fromisoformat(since_raw)
        except (TypeError, ValueError):
            continue
        if since >= cutoff:
            continue
        try:
            await db.workflowexecution.update(
                where={"id": row.id},
                data={
                    "status": "failed",
                    "error": _APPROVAL_TIMEOUT_ERROR_TEMPLATE.format(hours=timeout_hours),
                    "completedAt": current_time,
                },
            )
            marked += 1
        except Exception:
            logger.exception(
                "execution_sweeper: failed to mark expired approval %s as failed",
                getattr(row, "id", "<unknown>"),
            )

    if marked > 0:
        logger.info(
            "execution_sweeper: marked %d/%d waiting_approval rows as expired",
            marked,
            len(rows),
        )

    return SweepResult(scanned=len(rows), marked_failed=marked)


_LEASE_EXPIRY_ERROR_TEMPLATE = (
    "Execution's lease expired {attempts} times without completing "
    "(worker likely died mid-run each time). Dead-lettered by the "
    "lease-expiry sweeper after exceeding max_delivery_attempts."
)


async def sweep_expired_leases(
    db: Any,
    *,
    max_delivery_attempts: int,
    now: datetime | None = None,
) -> SweepResult:
    """Recover `running` executions whose lease expired without completing.

    A lease expiring means the worker that claimed the execution (via
    the claim-and-run endpoint's FOR UPDATE SKIP LOCKED) died before
    finishing — Cloud Run instance recycled, OOM, uncaught crash. Below
    max_delivery_attempts, clear the lease AND re-enqueue a fresh Cloud
    Task (clearing the lease alone is not enough — see the comment
    below). At or above max_delivery_attempts, mark it failed
    (dead-lettered) rather than retry indefinitely.

    Only touches rows with a non-NULL, expired `leaseExpiresAt` — a
    `running` row that never got a lease at all (e.g. crashed before
    claim-and-run set one) is not this function's concern; that coarser
    case is still covered by `sweep_stuck_executions`'s plain
    `startedAt`-based timeout (see its docstring).
    """
    if max_delivery_attempts <= 0:
        raise ValueError("max_delivery_attempts must be > 0")

    current_time = now or datetime.now(UTC)

    rows: list[Any] = await db.workflowexecution.find_many(
        where={
            "status": "running",
            "leaseExpiresAt": {"lt": current_time},
        },
    )

    marked_failed = 0
    errored = 0
    for row in rows:
        try:
            if row.deliveryAttempts >= max_delivery_attempts:
                await db.workflowexecution.update(
                    where={"id": row.id},
                    data={
                        "status": "failed",
                        "error": _LEASE_EXPIRY_ERROR_TEMPLATE.format(attempts=row.deliveryAttempts),
                        "completedAt": current_time,
                        "leaseOwner": None,
                        "leaseExpiresAt": None,
                    },
                )
                marked_failed += 1
            else:
                from src.execution.cloud_tasks import enqueue_execution

                # Preserve the original run vs. resume kind — a died
                # mid-resume execution must be re-enqueued as a resume
                # (so claim-and-run reads `_resume_decision` back out of
                # variables, per Task 13), not restarted as a fresh run.
                row_variables = getattr(row, "variables", None) or {}
                kind = (
                    "resume"
                    if isinstance(row_variables, dict) and "_resume_decision" in row_variables
                    else "run"
                )

                # Enqueue BEFORE clearing the lease — NOT the other way
                # around. If enqueue_execution raises (Cloud Tasks API
                # error, IAM blip, transient network failure), the
                # exception is caught below and this row's lease must
                # still be intact, because this function's own claim
                # filter (`"leaseExpiresAt": {"lt": current_time}`) only
                # ever matches a non-NULL, expired lease — SQL `NULL < x`
                # is never true. Clear the lease first and have enqueue
                # fail, and `leaseExpiresAt` becomes NULL forever: this
                # row would silently drop out of lease-based recovery for
                # good, even though max_delivery_attempts wasn't
                # exhausted. Enqueuing first is safe even on the success
                # path too: claim-and-run's own claim condition
                # (`lease_expires_at IS NULL OR lease_expires_at < now()`)
                # already tolerates an unexpired-but-stale lease, so a
                # duplicate delivery from double-enqueueing (e.g. if the
                # lease-clear below itself later fails) can never
                # double-run the execution — SELECT ... FOR UPDATE SKIP
                # LOCKED still arbitrates that.
                await enqueue_execution(row.id, kind=kind, db=db)

                await db.workflowexecution.update(
                    where={"id": row.id},
                    data={"leaseOwner": None, "leaseExpiresAt": None},
                )
        except Exception:
            errored += 1
            logger.exception(
                "execution_sweeper: failed to recover expired-lease execution %s",
                getattr(row, "id", "<unknown>"),
            )

    if rows:
        logger.info(
            "execution_sweeper: processed %d expired leases, %d dead-lettered, %d errored",
            len(rows),
            marked_failed,
            errored,
        )

    return SweepResult(scanned=len(rows), marked_failed=marked_failed, errored=errored)


async def sweep_old_execution_events(
    db: Any,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """Delete execution_events rows older than retention_days (P1-4:
    "add bounded retention and cleanup"). Unlike the other sweeps, this
    one is pure hygiene — event rows carry no operational state, only
    history — so a plain age-based bulk delete is sufficient; no
    per-row error handling or partial-failure bookkeeping is needed.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be > 0")
    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(days=retention_days)
    result = await db.executionevent.delete_many(where={"createdAt": {"lt": cutoff}})
    count = getattr(result, "count", result if isinstance(result, int) else 0)
    if count:
        logger.info("execution_sweeper: deleted %d execution_events past retention", count)
    return count


async def _sweeper_loop(
    db: Any,
    *,
    interval_seconds: int,
    stuck_after_seconds: int,
    approval_timeout_hours: int,
) -> None:
    """Background coroutine: run the sweeper on a fixed interval forever.

    Cancellation propagates cleanly via the `asyncio.CancelledError` path —
    FastAPI's lifespan exit cancels the task on shutdown.
    """
    while True:
        try:
            await sweep_stuck_executions(db, stuck_after_seconds=stuck_after_seconds)
            await sweep_expired_approvals(db, timeout_hours=approval_timeout_hours)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("execution_sweeper: unexpected error in loop")
        await asyncio.sleep(interval_seconds)


def start_sweeper(
    app: FastAPI,
    db: Any,
    *,
    interval_seconds: int,
    stuck_after_seconds: int,
    approval_timeout_hours: int,
) -> asyncio.Task[None] | None:
    """Schedule the sweeper as a background task on the FastAPI app.

    Returns the task handle (or `None` when disabled) so the lifespan can
    cancel it on shutdown.  `interval_seconds <= 0` disables the sweeper —
    useful for tests and for environments that prefer an external cron.
    """
    if interval_seconds <= 0:
        logger.info("execution_sweeper: disabled (interval_seconds=%d)", interval_seconds)
        return None

    task: asyncio.Task[None] = asyncio.create_task(
        _sweeper_loop(
            db,
            interval_seconds=interval_seconds,
            stuck_after_seconds=stuck_after_seconds,
            approval_timeout_hours=approval_timeout_hours,
        ),
        name="composer.execution_sweeper",
    )
    app.state.execution_sweeper_task = task
    logger.info(
        "execution_sweeper: started (interval=%ds, stuck_after=%ds, approval_timeout=%dh)",
        interval_seconds,
        stuck_after_seconds,
        approval_timeout_hours,
    )
    return task


async def stop_sweeper(task: asyncio.Task[None] | None) -> None:
    """Cancel the sweeper task and wait for graceful exit. Safe on `None`."""
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


__all__ = [
    "SweepResult",
    "start_sweeper",
    "stop_sweeper",
    "sweep_expired_approvals",
    "sweep_expired_leases",
    "sweep_old_execution_events",
    "sweep_stuck_executions",
]
