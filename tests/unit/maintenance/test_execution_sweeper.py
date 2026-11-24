"""Execution maintenance policy, including changes between read and write."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from src.db.repos import events as events_repo
from src.db.repos import executions as executions_repo
from src.maintenance import execution_sweeper as sweeper

if TYPE_CHECKING:
    from collections.abc import Callable


def _now() -> datetime:
    return datetime(2026, 11, 17, 12, tzinfo=UTC)


@dataclass
class _Row:
    id: str = "execution-1"
    status: str = "running"
    startedAt: datetime = field(default_factory=lambda: _now() - timedelta(days=30))
    runningSince: datetime | None = field(default_factory=lambda: _now() - timedelta(minutes=10))
    leaseExpiresAt: datetime | None = None
    leaseOwner: str | None = "worker-1"
    deliveryAttempts: int = 1
    variables: Any = field(default_factory=dict[str, Any])
    error: str | None = None
    failureCode: str | None = None
    completedAt: datetime | None = None

    @property
    def lease_expires_at(self) -> datetime | None:
        return self.leaseExpiresAt


@dataclass
class _ExecutionTable:
    """In-memory stand-in for the executions repo's scan and guarded-write functions.

    The SQL guards themselves are proven against Postgres by tests/integration/sqla_phase4a;
    this fake pins the sweeper's policy: which guard it hands the repo, per-row isolation and
    the counts.
    """

    rows: list[_Row] = field(default_factory=list[_Row])
    queries: list[str] = field(default_factory=list[str])
    writes: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    before_update: Callable[[_Row], None] | None = None
    fail_update_for_ids: set[str] = field(default_factory=set[str])
    commits: int = 0

    def scan(self, kind: str, predicate: Callable[[_Row], bool]) -> list[_Row]:
        self.queries.append(kind)
        return [deepcopy(row) for row in self.rows if predicate(row)]

    def write(
        self,
        guard: dict[str, Any],
        predicate: Callable[[_Row], bool],
        data: dict[str, Any],
    ) -> bool:
        self.writes.append(guard)
        for row in self.rows:
            if self.before_update is not None:
                self.before_update(row)
        if guard["id"] in self.fail_update_for_ids:
            raise RuntimeError("simulated db failure")
        won = False
        for row in self.rows:
            if row.id == guard["id"] and predicate(row):
                for key, value in data.items():
                    setattr(row, key, value)
                won = True
        return won


class _FakeSession:
    def __init__(self, table: _ExecutionTable) -> None:
        self.table = table

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        self.table.commits += 1


@dataclass
class _DbStub:
    """Callable like an ``async_sessionmaker``; every call opens a fresh fake session."""

    workflowexecution: _ExecutionTable = field(default_factory=_ExecutionTable)

    def __call__(self) -> _FakeSession:
        return _FakeSession(self.workflowexecution)


def _db(*rows: _Row) -> Any:
    return _DbStub(_ExecutionTable(list(rows)))


_CLEARED = {"leaseOwner": None, "leaseExpiresAt": None, "runningSince": None}


def _orphan_ok(row: _Row, cutoff: datetime) -> bool:
    return (
        row.status == "running"
        and row.leaseExpiresAt is None
        and row.runningSince is not None
        and row.runningSince < cutoff
    )


@pytest.fixture(autouse=True)
def _fake_repo(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    async def list_orphaned_running(session: _FakeSession, *, cutoff: datetime) -> list[_Row]:
        return session.table.scan("orphaned", lambda r: _orphan_ok(r, cutoff))

    async def fail_orphaned_if_unchanged(
        session: _FakeSession, execution_id: str, *, cutoff: datetime, error: str, now: datetime
    ) -> bool:
        return session.table.write(
            {"id": execution_id, "kind": "orphaned", "cutoff": cutoff},
            lambda r: _orphan_ok(r, cutoff),
            {
                "status": "failed",
                "failureCode": "orphaned_running",
                "error": error,
                "completedAt": now,
                **_CLEARED,
            },
        )

    async def list_waiting_approval(session: _FakeSession) -> list[_Row]:
        return session.table.scan("approval", lambda r: r.status == "waiting_approval")

    async def fail_expired_approval_if_unchanged(
        session: _FakeSession,
        execution_id: str,
        *,
        variables_snapshot: Any,
        error: str,
        now: datetime,
    ) -> bool:
        return session.table.write(
            {"id": execution_id, "kind": "approval", "variables": variables_snapshot},
            lambda r: r.status == "waiting_approval" and r.variables == variables_snapshot,
            {"status": "failed", "error": error, "completedAt": now},
        )

    async def list_expired_leases(session: _FakeSession, *, now: datetime) -> list[_Row]:
        return session.table.scan(
            "lease",
            lambda r: (
                r.status == "running" and r.leaseExpiresAt is not None and r.leaseExpiresAt < now
            ),
        )

    async def fail_expired_lease_if_unchanged(
        session: _FakeSession,
        execution_id: str,
        *,
        lease_expires_at: datetime,
        error: str,
        now: datetime,
    ) -> bool:
        return session.table.write(
            {"id": execution_id, "kind": "lease", "leaseExpiresAt": lease_expires_at},
            lambda r: r.status == "running" and r.leaseExpiresAt == lease_expires_at,
            {
                "status": "failed",
                "failureCode": "lease_expired",
                "error": error,
                "completedAt": now,
                **_CLEARED,
            },
        )

    for fn in (
        list_orphaned_running,
        fail_orphaned_if_unchanged,
        list_waiting_approval,
        fail_expired_approval_if_unchanged,
        list_expired_leases,
        fail_expired_lease_if_unchanged,
    ):
        monkeypatch.setattr(executions_repo, fn.__name__, fn)


@pytest.mark.parametrize("variables", [{}, {"_resume_decision": "approved"}])
async def test_expired_lease_fails_without_replaying(
    monkeypatch: pytest.MonkeyPatch, variables: dict[str, Any]
) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr("src.execution.cloud_tasks.enqueue_execution", enqueue)
    row = _Row(leaseExpiresAt=_now() - timedelta(seconds=1), variables=variables)
    db = _db(row)

    result = await sweeper.sweep_expired_leases(db, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=1)
    assert row.status == "failed"
    assert row.failureCode == "lease_expired"
    assert row.completedAt == _now()
    assert row.error and "lease" in row.error.lower()
    assert row.leaseOwner is None
    assert row.leaseExpiresAt is None
    assert row.runningSince is None
    enqueue.assert_not_called()
    assert db.workflowexecution.writes == [
        {"id": row.id, "kind": "lease", "leaseExpiresAt": _now() - timedelta(seconds=1)}
    ]
    assert db.workflowexecution.commits == 1


async def test_renewed_lease_wins_against_expiry_sweep() -> None:
    row = _Row(leaseExpiresAt=_now() - timedelta(seconds=1))
    db = _db(row)
    renewed_until = _now() + timedelta(minutes=30)

    def _renew(current: _Row) -> None:
        current.leaseExpiresAt = renewed_until

    db.workflowexecution.before_update = _renew

    result = await sweeper.sweep_expired_leases(db, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=0)
    assert row.status == "running"
    assert row.leaseExpiresAt == renewed_until
    assert row.failureCode is None
    assert row.completedAt is None


@pytest.mark.parametrize("lease", [None, _now(), _now() + timedelta(seconds=1)])
async def test_nonexpired_leases_are_not_selected_despite_old_creation(
    lease: datetime | None,
) -> None:
    row = _Row(leaseExpiresAt=lease)
    db = _db(row)

    result = await sweeper.sweep_expired_leases(db, now=_now())

    assert result == sweeper.SweepResult(scanned=0, marked_failed=0)
    assert row.status == "running"
    assert db.workflowexecution.writes == []


async def test_orphaned_running_fails_and_clears_attempt_fields() -> None:
    row = _Row()
    db = _db(row)

    result = await sweeper.sweep_orphaned_running(db, grace_seconds=120, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=1)
    assert row.status == "failed"
    assert row.failureCode == "orphaned_running"
    assert row.error and "120s" in row.error
    assert row.completedAt == _now()
    assert row.runningSince is None
    assert row.leaseExpiresAt is None
    assert row.leaseOwner is None
    assert db.workflowexecution.writes == [
        {"id": row.id, "kind": "orphaned", "cutoff": _now() - timedelta(seconds=120)}
    ]


@pytest.mark.parametrize(
    ("running_since", "lease"),
    [
        (_now() - timedelta(seconds=119), None),
        (_now() - timedelta(seconds=120), None),
        (None, None),
        (_now() - timedelta(days=1), _now() + timedelta(minutes=30)),
        (_now() - timedelta(days=1), _now() - timedelta(seconds=1)),
    ],
)
async def test_orphan_sweep_ignores_recent_attempts_and_all_leased_rows(
    running_since: datetime | None, lease: datetime | None
) -> None:
    row = _Row(runningSince=running_since, leaseExpiresAt=lease)
    db = _db(row)

    result = await sweeper.sweep_orphaned_running(db, grace_seconds=120, now=_now())

    assert result == sweeper.SweepResult(scanned=0, marked_failed=0)
    assert row.status == "running"
    assert db.workflowexecution.writes == []


@pytest.mark.parametrize("change", ["lease", "attempt", "cancel"])
async def test_orphan_sweep_rechecks_selection_at_write(change: str) -> None:
    row = _Row()
    db = _db(row)

    def _change(current: _Row) -> None:
        if change == "lease":
            current.leaseExpiresAt = _now() + timedelta(minutes=30)
        elif change == "attempt":
            current.runningSince = _now()
        else:
            current.status = "canceled"

    db.workflowexecution.before_update = _change

    result = await sweeper.sweep_orphaned_running(db, grace_seconds=120, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=0)
    assert row.failureCode is None
    assert row.completedAt is None
    assert row.status == ("canceled" if change == "cancel" else "running")


@pytest.mark.parametrize(
    "status", ["queued", "waiting_approval", "completed", "failed", "canceled"]
)
async def test_running_sweeps_never_select_other_statuses(status: str) -> None:
    leased = _Row(id="leased", status=status, leaseExpiresAt=_now() - timedelta(seconds=1))
    orphaned = _Row(id="orphaned", status=status)
    db = _db(leased, orphaned)

    lease_result = await sweeper.sweep_expired_leases(db, now=_now())
    orphan_result = await sweeper.sweep_orphaned_running(db, grace_seconds=120, now=_now())

    assert lease_result == orphan_result == sweeper.SweepResult(scanned=0, marked_failed=0)
    assert leased.status == orphaned.status == status


@pytest.mark.parametrize("status", ["waiting_approval", "completed", "canceled"])
async def test_lease_sweep_preserves_concurrent_status_transition(status: str) -> None:
    row = _Row(leaseExpiresAt=_now() - timedelta(seconds=1))
    db = _db(row)

    def _transition(current: _Row) -> None:
        current.status = status

    db.workflowexecution.before_update = _transition

    result = await sweeper.sweep_expired_leases(db, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=0)
    assert row.status == status
    assert row.failureCode is None


@pytest.mark.parametrize("sweep_kind", ["lease", "orphan"])
async def test_write_failure_does_not_stop_remaining_rows(sweep_kind: str) -> None:
    lease = _now() - timedelta(seconds=1) if sweep_kind == "lease" else None
    bad, good = _Row(id="bad", leaseExpiresAt=lease), _Row(id="good", leaseExpiresAt=lease)
    db = _db(bad, good)
    db.workflowexecution.fail_update_for_ids.add("bad")

    if sweep_kind == "lease":
        result = await sweeper.sweep_expired_leases(db, now=_now())
    else:
        result = await sweeper.sweep_orphaned_running(db, grace_seconds=120, now=_now())

    assert result == sweeper.SweepResult(scanned=2, marked_failed=1, errored=1)
    assert bad.status == "running"
    assert good.status == "failed"


@pytest.mark.parametrize("grace", [0, -1])
async def test_orphan_grace_must_be_positive(grace: int) -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        await sweeper.sweep_orphaned_running(_db(), grace_seconds=grace)


def _approval_row(**kwargs: Any) -> _Row:
    return _Row(
        status="waiting_approval",
        runningSince=None,
        leaseOwner=None,
        variables={"_pending_approval_since": (_now() - timedelta(hours=200)).isoformat()},
        **kwargs,
    )


async def test_approval_timeout_zero_skips_query() -> None:
    row = _approval_row()
    db = _db(row)

    result = await sweeper.sweep_expired_approvals(db, timeout_hours=0, now=_now())

    assert result == sweeper.SweepResult(scanned=0, marked_failed=0)
    assert row.status == "waiting_approval"
    assert db.workflowexecution.queries == []


async def test_positive_approval_timeout_fails_old_pause_with_conditional_write() -> None:
    row = _approval_row()
    db = _db(row)

    result = await sweeper.sweep_expired_approvals(db, timeout_hours=168, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=1)
    assert row.status == "failed"
    assert row.completedAt == _now()
    assert row.error and "expired waiting for approval" in row.error.lower()
    [guard] = db.workflowexecution.writes
    assert guard["id"] == row.id
    assert guard["kind"] == "approval"
    assert guard["variables"] == {
        "_pending_approval_since": (_now() - timedelta(hours=200)).isoformat()
    }


@pytest.mark.parametrize("change", ["new_pause", "resume", "cancel"])
async def test_approval_sweep_preserves_new_pause_or_status(change: str) -> None:
    row = _approval_row()
    db = _db(row)

    def _change(current: _Row) -> None:
        if change == "new_pause":
            current.variables = {"_pending_approval_since": _now().isoformat()}
        else:
            current.status = "running" if change == "resume" else "canceled"

    db.workflowexecution.before_update = _change

    result = await sweeper.sweep_expired_approvals(db, timeout_hours=168, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=0)
    assert (
        row.status
        == {"new_pause": "waiting_approval", "resume": "running", "cancel": "canceled"}[change]
    )
    assert row.completedAt is None


@pytest.mark.parametrize(
    "variables",
    [
        {},
        None,
        [],
        {"_pending_approval_since": "not-a-date"},
        {"_pending_approval_since": 4},
        {"_pending_approval_since": "2026-11-01T12:00:00"},
        {"_pending_approval_since": (_now() - timedelta(hours=1)).isoformat()},
        {"_pending_approval_since": (_now() - timedelta(hours=168)).isoformat()},
    ],
)
async def test_approval_sweep_skips_missing_malformed_or_recent_timestamp(variables: Any) -> None:
    row = _approval_row()
    row.variables = variables
    db = _db(row)

    result = await sweeper.sweep_expired_approvals(db, timeout_hours=168, now=_now())

    assert result == sweeper.SweepResult(scanned=1, marked_failed=0)
    assert row.status == "waiting_approval"


async def test_approval_write_failure_does_not_stop_remaining_rows() -> None:
    bad, good = _approval_row(id="bad"), _approval_row(id="good")
    db = _db(bad, good)
    db.workflowexecution.fail_update_for_ids.add("bad")

    result = await sweeper.sweep_expired_approvals(db, timeout_hours=168, now=_now())

    assert result == sweeper.SweepResult(scanned=2, marked_failed=1, errored=1)
    assert bad.status == "waiting_approval"
    assert good.status == "failed"


async def test_approval_negative_timeout_rejected() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        await sweeper.sweep_expired_approvals(_db(), timeout_hours=-1)


async def test_start_sweeper_disabled_returns_none() -> None:
    from fastapi import FastAPI

    assert (
        sweeper.start_sweeper(
            FastAPI(),
            _db(),
            interval_seconds=0,
            orphaned_running_grace_seconds=120,
            approval_timeout_hours=0,
        )
        is None
    )


@pytest.mark.parametrize("timeout_hours", [0, 168])
async def test_background_loop_runs_orphan_sweep_and_optional_approvals(
    monkeypatch: pytest.MonkeyPatch, timeout_hours: int
) -> None:
    from fastapi import FastAPI

    row = _Row(runningSince=datetime(2000, 1, 1, tzinfo=UTC))
    db = _db(row)
    approvals = AsyncMock(return_value=sweeper.SweepResult(scanned=0, marked_failed=0))
    monkeypatch.setattr(sweeper, "sweep_expired_approvals", approvals)

    task = sweeper.start_sweeper(
        FastAPI(),
        db,
        interval_seconds=300,
        orphaned_running_grace_seconds=120,
        approval_timeout_hours=timeout_hours,
    )
    assert task is not None
    await asyncio.sleep(0)
    await sweeper.stop_sweeper(task)

    assert task.cancelled()
    assert row.failureCode == "orphaned_running"
    if timeout_hours:
        approvals.assert_awaited_once_with(db, timeout_hours=168)
    else:
        approvals.assert_not_awaited()


async def test_sweep_old_execution_events_deletes_past_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db()
    delete = AsyncMock(return_value=42)
    monkeypatch.setattr(events_repo, "delete_older_than", delete)

    deleted = await sweeper.sweep_old_execution_events(db, retention_days=30, now=_now())

    assert deleted == 42
    delete.assert_awaited_once()
    assert delete.await_args is not None
    assert delete.await_args.args[1] == _now() - timedelta(days=30)
    assert db.workflowexecution.commits == 1


@pytest.mark.parametrize("days", [0, -1])
async def test_sweep_old_execution_events_rejects_non_positive_retention(days: int) -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        await sweeper.sweep_old_execution_events(_db(), retention_days=days)
