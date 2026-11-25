"""Fail abandoned processing attempts without replaying workflow side effects.

Expired leases identify claimed attempts whose workers stopped responding.
Lease-less running rows are abandoned only after their current attempt's
claim grace period. Every failure write rechecks the state read by the
sweep so concurrent lease renewal, cancellation, or approval can win.

Production invokes all maintenance through ``POST /internal/sweep``. The
optional in-process loop handles orphaned attempts and approval expiry for
local deployments; it does not depend on background CPU in Cloud Run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.repos import events as events_repo
from src.db.repos import executions as executions_repo

if TYPE_CHECKING:
    from fastapi import FastAPI

SessionMaker = async_sessionmaker[AsyncSession]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    """Selected rows, successful conditional failures, and per-row errors.

    A lost compare-and-set race counts as scanned but neither failed nor
    errored: a concurrent transition remains authoritative.
    """

    scanned: int
    marked_failed: int
    errored: int = 0


async def sweep_orphaned_running(
    sessionmaker: SessionMaker,
    *,
    grace_seconds: int,
    now: datetime | None = None,
) -> SweepResult:
    """Fail running attempts that have remained without a lease past grace."""
    if grace_seconds <= 0:
        raise ValueError("grace_seconds must be > 0")

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(seconds=grace_seconds)
    async with sessionmaker() as session:
        rows = await executions_repo.list_orphaned_running(session, cutoff=cutoff)

    marked = 0
    errored = 0
    for row in rows:
        try:
            async with sessionmaker() as session:
                won = await executions_repo.fail_orphaned_if_unchanged(
                    session,
                    row.id,
                    cutoff=cutoff,
                    error=(
                        f"Execution remained running without a lease for more than {grace_seconds}s. "
                        "Marked failed by the orphaned-running sweeper."
                    ),
                    now=current_time,
                )
                await session.commit()
            marked += 1 if won else 0
        except Exception:
            errored += 1
            logger.exception(
                "execution_sweeper: failed to mark orphaned execution %s as failed",
                row.id,
            )

    if rows:
        logger.info(
            "execution_sweeper: scanned %d orphaned attempts, %d failed, %d errored",
            len(rows),
            marked,
            errored,
        )
    return SweepResult(scanned=len(rows), marked_failed=marked, errored=errored)


_APPROVAL_TIMEOUT_ERROR_TEMPLATE = (
    "Execution expired waiting for approval after {hours}h with no decision. "
    "Auto-failed by the approval-timeout sweeper."
)


async def sweep_expired_approvals(
    sessionmaker: SessionMaker,
    *,
    timeout_hours: int,
    now: datetime | None = None,
) -> SweepResult:
    """Expire old approval pauses only when a positive timeout is configured."""
    if timeout_hours < 0:
        raise ValueError("timeout_hours must be >= 0")
    if timeout_hours == 0:
        return SweepResult(scanned=0, marked_failed=0)

    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(hours=timeout_hours)
    async with sessionmaker() as session:
        rows = await executions_repo.list_waiting_approval(session)

    marked = 0
    errored = 0
    for row in rows:
        variables: dict[str, Any] = row.variables or {}
        since_raw = (
            variables.get("_pending_approval_since")
            if isinstance(cast("Any", variables), dict)
            else None
        )
        if not since_raw:
            continue
        try:
            since = datetime.fromisoformat(since_raw)
        except (TypeError, ValueError):
            continue
        if since.tzinfo is None or since >= cutoff:
            continue
        try:
            # Recheck the entire snapshot so a resumed execution or a newer
            # approval pause cannot be expired.
            async with sessionmaker() as session:
                won = await executions_repo.fail_expired_approval_if_unchanged(
                    session,
                    row.id,
                    variables_snapshot=variables,
                    error=_APPROVAL_TIMEOUT_ERROR_TEMPLATE.format(hours=timeout_hours),
                    now=current_time,
                )
                await session.commit()
            marked += 1 if won else 0
        except Exception:
            errored += 1
            logger.exception(
                "execution_sweeper: failed to mark expired approval %s as failed",
                row.id,
            )

    if rows:
        logger.info(
            "execution_sweeper: scanned %d approval pauses, %d expired, %d errored",
            len(rows),
            marked,
            errored,
        )
    return SweepResult(scanned=len(rows), marked_failed=marked, errored=errored)


async def sweep_expired_leases(
    sessionmaker: SessionMaker,
    *,
    now: datetime | None = None,
) -> SweepResult:
    """Conditionally fail expired leased attempts; never restart their work."""
    current_time = now or datetime.now(UTC)
    async with sessionmaker() as session:
        rows = await executions_repo.list_expired_leases(session, now=current_time)

    marked = 0
    errored = 0
    for row in rows:
        try:
            if row.lease_expires_at is None:  # unreachable: the scan filters on it
                continue
            async with sessionmaker() as session:
                won = await executions_repo.fail_expired_lease_if_unchanged(
                    session,
                    row.id,
                    lease_expires_at=row.lease_expires_at,
                    error=(
                        "Execution's processing lease expired without completing. "
                        "Marked failed by the lease-expiry sweeper."
                    ),
                    now=current_time,
                )
                await session.commit()
            marked += 1 if won else 0
        except Exception:
            errored += 1
            logger.exception(
                "execution_sweeper: failed to mark expired-lease execution %s as failed",
                row.id,
            )

    if rows:
        logger.info(
            "execution_sweeper: scanned %d expired leases, %d failed, %d errored",
            len(rows),
            marked,
            errored,
        )
    return SweepResult(scanned=len(rows), marked_failed=marked, errored=errored)


async def sweep_old_execution_events(
    sessionmaker: SessionMaker,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """Delete execution events older than the configured retention period."""
    if retention_days <= 0:
        raise ValueError("retention_days must be > 0")
    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(days=retention_days)
    async with sessionmaker() as session:
        count = await events_repo.delete_older_than(session, cutoff)
        await session.commit()
    if count:
        logger.info("execution_sweeper: deleted %d execution_events past retention", count)
    return count


async def _sweeper_loop(
    sessionmaker: SessionMaker,
    *,
    interval_seconds: int,
    orphaned_running_grace_seconds: int,
    approval_timeout_hours: int,
) -> None:
    """Run local maintenance until the lifespan cancels this task."""
    while True:
        try:
            await sweep_orphaned_running(sessionmaker, grace_seconds=orphaned_running_grace_seconds)
            if approval_timeout_hours > 0:
                await sweep_expired_approvals(sessionmaker, timeout_hours=approval_timeout_hours)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("execution_sweeper: unexpected error in loop")
        await asyncio.sleep(interval_seconds)


def start_sweeper(
    app: FastAPI,
    sessionmaker: SessionMaker,
    *,
    interval_seconds: int,
    orphaned_running_grace_seconds: int,
    approval_timeout_hours: int,
) -> asyncio.Task[None] | None:
    """Start local maintenance; an interval <= 0 disables the background task."""
    if interval_seconds <= 0:
        logger.info("execution_sweeper: disabled (interval_seconds=%d)", interval_seconds)
        return None

    task: asyncio.Task[None] = asyncio.create_task(
        _sweeper_loop(
            sessionmaker,
            interval_seconds=interval_seconds,
            orphaned_running_grace_seconds=orphaned_running_grace_seconds,
            approval_timeout_hours=approval_timeout_hours,
        ),
        name="composer.execution_sweeper",
    )
    app.state.execution_sweeper_task = task
    logger.info(
        "execution_sweeper: started (interval=%ds, orphan_grace=%ds, approval_timeout=%dh)",
        interval_seconds,
        orphaned_running_grace_seconds,
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
    "sweep_orphaned_running",
]
