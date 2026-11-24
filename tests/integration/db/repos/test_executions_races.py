"""Concurrency tests: each guarded transition has exactly one winner (real Postgres, separate sessions)."""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import Workflow, WorkflowExecution
from src.db.repos import executions as repo

pytestmark = pytest.mark.integration

REPEATS = 10
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
LEASE = datetime(2030, 1, 1, 11, 0, 0, tzinfo=UTC)
RUNNING_SINCE = datetime(2030, 1, 1, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
async def sm(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def wid(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[str]:
    """A committed workflow; deleting it cascades away every execution the test made."""
    async with sm() as s:
        w = Workflow(name="race", nodes=[], edges=[])
        s.add(w)
        await s.commit()
        workflow_id = w.id
    try:
        yield workflow_id
    finally:
        async with sm() as s:
            await s.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await s.commit()


async def _seed(sm: async_sessionmaker[AsyncSession], wid: str, status: str, **kw: Any) -> str:
    values: dict[str, Any] = {
        "workflow_id": wid,
        "user_id": "owner",
        "status": status,
        "node_results": {},
        "variables": {"a": 1},
        "thread_id": f"th-{uuid.uuid4()}",
        "lease_owner": None,
        "lease_expires_at": None,
        "running_since": None,
    }
    values.update(kw)
    async with sm() as s:
        row = WorkflowExecution(**values)
        s.add(row)
        await s.commit()
        return row.id


async def _row(sm: async_sessionmaker[AsyncSession], eid: str) -> repo.ExecutionRecord:
    async with sm() as s:
        rec = await repo.get(s, eid)
    assert rec is not None
    return rec


def _in_own_session(
    sm: async_sessionmaker[AsyncSession],
    fn: Callable[[AsyncSession], Awaitable[Any]],
) -> Callable[[], Awaitable[Any]]:
    async def run() -> Any:
        async with sm() as s:
            result = await fn(s)
            await s.commit()
            return result

    return run


async def _claim(sm: async_sessionmaker[AsyncSession], eid: str, worker: str) -> Any:
    async def go(s: AsyncSession) -> Any:
        return await repo.claim(
            s,
            eid,
            kind="run",
            worker_id=worker,
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )

    return await _in_own_session(sm, go)()


def _cancel_of(eid: str) -> Callable[[AsyncSession], Awaitable[bool]]:
    async def op(s: AsyncSession) -> bool:
        return await repo.cancel_if_active(s, eid, error="canceled", now=NOW)

    return op


def _complete_of(eid: str) -> Callable[[AsyncSession], Awaitable[bool]]:
    async def op(s: AsyncSession) -> bool:
        return await repo.mark_completed_if_running(
            s, eid, output={"r": 1}, variables={"v": 1}, node_results={}, now=NOW
        )

    return op


async def test_five_concurrent_claims_have_exactly_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "queued")
        results = await asyncio.gather(*[_claim(sm, eid, f"w{i}") for i in range(5)])
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        rec = await _row(sm, eid)
        assert rec.delivery_attempts == 1 and rec.status == "running"
        assert rec.lease_owner is not None and rec.lease_owner.startswith("w")


async def test_claim_skips_a_row_locked_by_another_transaction_without_waiting(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "queued")
        async with sm() as holder:
            await holder.execute(
                select(WorkflowExecution.id).where(WorkflowExecution.id == eid).with_for_update()
            )
            started = time.monotonic()
            assert await _claim(sm, eid, "w") is None
            assert time.monotonic() - started < 1.0
            await holder.rollback()
        rec = await _row(sm, eid)
        assert rec.status == "queued" and rec.delivery_attempts == 0
        assert await _claim(sm, eid, "w") is not None  # claimable once the lock is gone


async def _race(
    sm: async_sessionmaker[AsyncSession],
    *fns: Callable[[AsyncSession], Awaitable[bool]],
) -> list[bool]:
    return list(await asyncio.gather(*[_in_own_session(sm, fn)() for fn in fns]))


async def test_cancel_versus_completion_has_exactly_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for i in range(REPEATS):
        eid = await _seed(sm, wid, "running", lease_owner="w", lease_expires_at=LEASE)
        fns = [_cancel_of(eid), _complete_of(eid)]
        if i % 2:
            fns.reverse()
        results = await _race(sm, *fns)
        assert sum(results) == 1
        rec = await _row(sm, eid)
        cancel_won = results[0] if i % 2 == 0 else results[1]
        assert rec.status == ("canceled" if cancel_won else "completed")
        assert rec.lease_owner is None and rec.completed_at == NOW
        assert (rec.output is not None) == (not cancel_won)


async def test_five_concurrent_cancels_have_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "queued")
        results = await _race(sm, *[_cancel_of(eid) for _ in range(5)])
        assert sum(results) == 1
        assert (await _row(sm, eid)).status == "canceled"


async def test_lease_sweep_versus_completion_has_exactly_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "running", lease_owner="w", lease_expires_at=LEASE)

        async def sweep(s: AsyncSession, e: str = eid) -> bool:
            return await repo.fail_expired_lease_if_unchanged(
                s, e, lease_expires_at=LEASE, error="lease", now=NOW
            )

        results = await _race(sm, sweep, _complete_of(eid))
        assert sum(results) == 1
        rec = await _row(sm, eid)
        assert rec.status == ("failed" if results[0] else "completed")
        assert (rec.failure_code == "lease_expired") == results[0]


async def test_lease_sweep_with_a_stale_scanned_expiry_loses_after_renewal(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    eid = await _seed(sm, wid, "running", lease_owner="w", lease_expires_at=LEASE)
    async with sm() as s:
        scanned = [r for r in await repo.list_expired_leases(s, now=NOW) if r.id == eid]
    assert len(scanned) == 1
    renewed = LEASE + timedelta(hours=2)
    async with sm() as s:  # a renewal between the scan and the write
        await s.execute(
            sa_update(WorkflowExecution)
            .where(WorkflowExecution.id == eid)
            .values(lease_expires_at=renewed)
        )
        await s.commit()
    async with sm() as s:
        assert not await repo.fail_expired_lease_if_unchanged(
            s, eid, lease_expires_at=scanned[0].lease_expires_at or LEASE, error="x", now=NOW
        )
    rec = await _row(sm, eid)
    assert rec.status == "running" and rec.lease_expires_at == renewed


async def test_orphan_sweep_loses_after_a_claim_set_the_lease(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "running", running_since=RUNNING_SINCE)
        cutoff = RUNNING_SINCE + timedelta(hours=1)
        async with sm() as s:
            assert eid in {r.id for r in await repo.list_orphaned_running(s, cutoff=cutoff)}
        async with sm() as s:  # a lease is set between the scan and the write
            await s.execute(
                sa_update(WorkflowExecution)
                .where(WorkflowExecution.id == eid)
                .values(lease_owner="w", lease_expires_at=NOW + timedelta(hours=1))
            )
            await s.commit()
        async with sm() as s:
            assert not await repo.fail_orphaned_if_unchanged(
                s, eid, cutoff=cutoff, error="x", now=NOW
            )
        assert (await _row(sm, eid)).status == "running"


async def test_approval_sweep_loses_when_the_variables_changed_since_the_scan(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(
            sm, wid, "waiting_approval", variables={"_pending_approval_since": "2020-01-01"}
        )
        async with sm() as s:
            scanned = next(r for r in await repo.list_waiting_approval(s) if r.id == eid)
        async with sm() as s:  # re-paused with a newer marker after the scan
            await s.execute(
                sa_update(WorkflowExecution)
                .where(WorkflowExecution.id == eid)
                .values(variables={"_pending_approval_since": "2020-02-02"})
            )
            await s.commit()
        async with sm() as s:
            assert not await repo.fail_expired_approval_if_unchanged(
                s, eid, variables_snapshot=scanned.variables, error="x", now=NOW
            )
        assert (await _row(sm, eid)).status == "waiting_approval"


async def test_approval_sweep_versus_cancel_has_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "waiting_approval")

        async def sweep(s: AsyncSession, e: str = eid) -> bool:
            return await repo.fail_expired_approval_if_unchanged(
                s, e, variables_snapshot={"a": 1}, error="expired", now=NOW
            )

        results = await _race(sm, sweep, _cancel_of(eid))
        assert sum(results) == 1


async def test_pause_versus_cancel_never_pauses_a_canceled_row(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    """A pause then a cancel is legal (waiting_approval is cancellable); a pause AFTER a cancel
    must lose, so a cancel that wins first means no approval pause is recorded."""
    outcomes: set[tuple[bool, bool]] = set()
    for i in range(REPEATS):
        eid = await _seed(sm, wid, "running", lease_owner="w", lease_expires_at=LEASE)

        async def pause(s: AsyncSession, e: str = eid) -> bool:
            return await repo.mark_waiting_approval_if_running(s, e, variables={"p": 1})

        fns = [pause, _cancel_of(eid)]
        if i % 2:
            fns.reverse()
        results = await _race(sm, *fns)
        paused, canceled = (results[0], results[1]) if i % 2 == 0 else (results[1], results[0])
        outcomes.add((paused, canceled))
        assert canceled  # cancel always lands: running and waiting_approval are both active
        rec = await _row(sm, eid)
        assert rec.status == "canceled"
        assert (rec.variables == {"p": 1}) == paused  # a lost pause wrote nothing
    assert all(canceled for _, canceled in outcomes)


async def test_failure_versus_cancel_has_exactly_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "running", lease_owner="w", lease_expires_at=LEASE)

        async def fail(s: AsyncSession, e: str = eid) -> bool:
            return await repo.mark_failed_if_running(
                s, e, error="E: x", failure_code="execution_error", now=NOW
            )

        results = await _race(sm, fail, _cancel_of(eid))
        assert sum(results) == 1
        assert (await _row(sm, eid)).status == ("failed" if results[0] else "canceled")


async def test_enqueue_failure_versus_cancel_leaves_a_cancel_alone(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "queued")

        async def enqueue_failed(s: AsyncSession, e: str = eid) -> bool:
            return await repo.mark_enqueue_failed_if_queued(s, e, error="boom", now=NOW)

        results = await _race(sm, enqueue_failed, _cancel_of(eid))
        assert sum(results) == 1
        assert (await _row(sm, eid)).status == ("failed" if results[0] else "canceled")
