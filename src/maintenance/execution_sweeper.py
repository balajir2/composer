"""Stuck-execution sweeper.

A `WorkflowExecution` row is created with `status='running'` the moment the
API accepts a request, and gets flipped to `completed`/`failed`/`waiting_approval`
by `LangGraphExecutor.run()` once the graph finishes.  The flip is awaited so
under normal conditions the row never lingers in `running`.

Three real-world failure modes still leave rows stuck:

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
  - `start_sweeper(app, ...)` — schedules the function on a recurring
    background task tied to the FastAPI lifespan.  Idempotent if the
    interval is 0 (sweeper disabled).

The sweeper only touches rows in `status='running'`.  `waiting_approval`
rows are excluded — they're paused on purpose and may sit for days waiting
for a human reviewer.
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
    """Outcome of a single sweeper run, for logging + tests."""

    scanned: int
    marked_failed: int


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


async def _sweeper_loop(
    db: Any,
    *,
    interval_seconds: int,
    stuck_after_seconds: int,
) -> None:
    """Background coroutine: run the sweeper on a fixed interval forever.

    Cancellation propagates cleanly via the `asyncio.CancelledError` path —
    FastAPI's lifespan exit cancels the task on shutdown.
    """
    while True:
        try:
            await sweep_stuck_executions(db, stuck_after_seconds=stuck_after_seconds)
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
        ),
        name="composer.execution_sweeper",
    )
    app.state.execution_sweeper_task = task
    logger.info(
        "execution_sweeper: started (interval=%ds, stuck_after=%ds)",
        interval_seconds,
        stuck_after_seconds,
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
    "sweep_stuck_executions",
]
