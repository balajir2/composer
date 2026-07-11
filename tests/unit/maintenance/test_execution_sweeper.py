"""Tests for the stuck-execution sweeper.

The sweeper finds `WorkflowExecution` rows that say `running` for longer
than the configured threshold and marks them `failed` with an explanatory
error.  The tests use an in-memory stub of the Prisma client so the
sweep policy is exercised without touching Postgres.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.maintenance.execution_sweeper import (
    SweepResult,
    start_sweeper,
    stop_sweeper,
    sweep_expired_approvals,
    sweep_stuck_executions,
)


@dataclass
class _Row:
    id: str
    status: str
    startedAt: datetime
    error: str | None = None
    completedAt: datetime | None = None
    variables: dict[str, Any] | None = None


@dataclass
class _ExecutionTable:
    rows: list[_Row] = field(default_factory=list)
    update_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_update_for_ids: set[str] = field(default_factory=set)

    async def find_many(self, *, where: dict[str, Any]) -> list[_Row]:
        out: list[_Row] = []
        cutoff: datetime | None = None
        if isinstance(where.get("startedAt"), dict):
            cutoff = where["startedAt"].get("lt")
        target_status = where.get("status")
        for r in self.rows:
            if target_status is not None and r.status != target_status:
                continue
            if cutoff is not None and not (r.startedAt < cutoff):
                continue
            out.append(r)
        return out

    async def update(self, *, where: dict[str, Any], data: dict[str, Any]) -> _Row:
        row_id = where["id"]
        if row_id in self.fail_update_for_ids:
            raise RuntimeError("simulated db failure")
        for r in self.rows:
            if r.id == row_id:
                self.update_calls.append((row_id, data))
                if "status" in data:
                    r.status = data["status"]
                if "error" in data:
                    r.error = data["error"]
                if "completedAt" in data:
                    r.completedAt = data["completedAt"]
                return r
        raise KeyError(row_id)


@dataclass
class _DbStub:
    workflowexecution: _ExecutionTable = field(default_factory=_ExecutionTable)


def _now() -> datetime:
    return datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


async def test_old_running_rows_are_marked_failed() -> None:
    """A `running` row older than the threshold is flipped to `failed`."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-old", status="running", startedAt=now - timedelta(minutes=20)),
    ]

    result = await sweep_stuck_executions(db, stuck_after_seconds=900, now=now)

    assert result == SweepResult(scanned=1, marked_failed=1)
    [(exec_id, data)] = db.workflowexecution.update_calls
    assert exec_id == "exe-old"
    assert data["status"] == "failed"
    assert "900s" in data["error"]
    assert data["completedAt"] == now


async def test_recent_running_rows_are_skipped() -> None:
    """Anything within the threshold is left alone."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-recent", status="running", startedAt=now - timedelta(seconds=60)),
    ]

    result = await sweep_stuck_executions(db, stuck_after_seconds=900, now=now)

    assert result == SweepResult(scanned=0, marked_failed=0)
    assert db.workflowexecution.update_calls == []


async def test_waiting_approval_rows_are_skipped() -> None:
    """`waiting_approval` is paused on purpose and never gets reaped."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(
            id="exe-waiting",
            status="waiting_approval",
            startedAt=now - timedelta(days=2),
        ),
    ]

    result = await sweep_stuck_executions(db, stuck_after_seconds=900, now=now)

    assert result == SweepResult(scanned=0, marked_failed=0)
    assert db.workflowexecution.update_calls == []


async def test_completed_rows_are_skipped() -> None:
    """Sanity: terminal rows are never re-touched."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-done", status="completed", startedAt=now - timedelta(hours=1)),
        _Row(id="exe-failed", status="failed", startedAt=now - timedelta(hours=1)),
    ]

    result = await sweep_stuck_executions(db, stuck_after_seconds=900, now=now)

    assert result == SweepResult(scanned=0, marked_failed=0)


async def test_one_failed_update_does_not_stop_other_rows() -> None:
    """If updating one row throws, the sweeper logs and continues."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-bad", status="running", startedAt=now - timedelta(minutes=20)),
        _Row(id="exe-good", status="running", startedAt=now - timedelta(minutes=20)),
    ]
    db.workflowexecution.fail_update_for_ids = {"exe-bad"}

    result = await sweep_stuck_executions(db, stuck_after_seconds=900, now=now)

    # 2 scanned, only 1 marked (the good one); no exception bubbled out.
    assert result == SweepResult(scanned=2, marked_failed=1)
    [(exec_id, _)] = db.workflowexecution.update_calls
    assert exec_id == "exe-good"


async def test_zero_threshold_rejected() -> None:
    """Misconfiguration — refuse to sweep with a 0 threshold."""
    db = _DbStub()
    with pytest.raises(ValueError, match="must be > 0"):
        await sweep_stuck_executions(db, stuck_after_seconds=0)


async def test_start_sweeper_disabled_returns_none() -> None:
    """interval_seconds=0 disables the sweeper entirely."""
    from fastapi import FastAPI

    app = FastAPI()
    db = _DbStub()
    task = start_sweeper(
        app, db, interval_seconds=0, stuck_after_seconds=900, approval_timeout_hours=168
    )
    assert task is None


async def test_start_and_stop_sweeper_runs_at_least_once() -> None:
    """Smoke-test the loop: starts, sweeps once, exits cleanly."""
    from fastapi import FastAPI

    app = FastAPI()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-old", status="running", startedAt=_now() - timedelta(hours=1)),
    ]

    # interval_seconds=1 keeps the loop responsive enough for the test to
    # observe the first sweep before cancelling.
    task = start_sweeper(
        app, db, interval_seconds=1, stuck_after_seconds=900, approval_timeout_hours=168
    )
    assert task is not None
    # Yield to the event loop so the loop body runs once.
    await asyncio.sleep(0.05)
    await stop_sweeper(task)
    assert task.cancelled() or task.done()
    # First iteration completed the sweep before the cancel.
    assert any(call[1].get("status") == "failed" for call in db.workflowexecution.update_calls)


async def test_stale_waiting_approval_rows_are_marked_failed() -> None:
    now = _now()
    stale_since = (now - timedelta(hours=200)).isoformat()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(
            id="exe-stale",
            status="waiting_approval",
            startedAt=now - timedelta(hours=200),
            variables={"_pending_approval_since": stale_since},
        ),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=1, marked_failed=1)
    [(exec_id, data)] = db.workflowexecution.update_calls
    assert exec_id == "exe-stale"
    assert data["status"] == "failed"
    assert "expired waiting for approval" in data["error"].lower()
    assert data["completedAt"] == now


async def test_recent_waiting_approval_rows_are_skipped() -> None:
    now = _now()
    fresh_since = (now - timedelta(hours=1)).isoformat()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(
            id="exe-fresh",
            status="waiting_approval",
            startedAt=now - timedelta(hours=1),
            variables={"_pending_approval_since": fresh_since},
        ),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=1, marked_failed=0)
    assert db.workflowexecution.update_calls == []


async def test_waiting_approval_rows_without_timestamp_are_skipped() -> None:
    """Defensive: a row somehow missing the stamp (e.g. pre-migration data)
    is left alone rather than immediately expired."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(
            id="exe-no-ts",
            status="waiting_approval",
            startedAt=now - timedelta(days=30),
            variables={},
        ),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=1, marked_failed=0)
    assert db.workflowexecution.update_calls == []


async def test_running_rows_are_not_touched_by_approval_sweep() -> None:
    """sweep_expired_approvals only ever looks at waiting_approval — a stuck
    running row is sweep_stuck_executions's job, not this one's."""
    now = _now()
    db = _DbStub()
    db.workflowexecution.rows = [
        _Row(id="exe-running", status="running", startedAt=now - timedelta(hours=200)),
    ]

    result = await sweep_expired_approvals(db, timeout_hours=168, now=now)

    assert result == SweepResult(scanned=0, marked_failed=0)


async def test_approval_sweep_zero_timeout_rejected() -> None:
    db = _DbStub()
    with pytest.raises(ValueError, match="must be > 0"):
        await sweep_expired_approvals(db, timeout_hours=0)
