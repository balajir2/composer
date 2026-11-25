"""Execution-attempt repository behavior against PostgreSQL."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    AttemptResult,
    AttemptTriggerKind,
    Workflow,
    WorkflowExecution,
)
from src.db.repos import execution_attempts as repo

pytestmark = pytest.mark.integration


@pytest.fixture
async def executions(session: AsyncSession) -> AsyncIterator[tuple[str, str]]:
    wf = Workflow(name="attempts", nodes=[], edges=[])
    session.add(wf)
    await session.flush()
    first = WorkflowExecution(
        workflow_id=wf.id,
        status="running",
        node_results={},
        variables={},
        thread_id="attempt-a",
        delivery_attempts=9,
    )
    second = WorkflowExecution(
        workflow_id=wf.id,
        status="running",
        node_results={},
        variables={},
        thread_id="attempt-b",
        delivery_attempts=0,
    )
    session.add_all([first, second])
    await session.flush()
    yield first.id, second.id


async def _open(
    session: AsyncSession,
    execution_id: str,
    *,
    started: datetime,
    lease: datetime,
    trigger: AttemptTriggerKind = AttemptTriggerKind.RUN,
    recovery: str | None = None,
    approval: str | None = None,
) -> repo.AttemptRecord:
    return await repo.open_for_claim(
        session,
        execution_id=execution_id,
        started_at=started,
        lease_owner="worker-1",
        lease_expires_at=lease,
        trigger_kind=trigger,
        approval_id=approval,
        actor_user_id="user-1",
        recovery_source_attempt_id=recovery,
        reason_ref="reason-1",
    )


async def test_open_uses_history_ordinal_and_returns_frozen_detached_record(
    session: AsyncSession, executions: tuple[str, str]
) -> None:
    eid, _ = executions
    start = datetime(2026, 11, 25, 12, tzinfo=UTC)
    expiry = start + timedelta(minutes=4)
    rec = await _open(session, eid, started=start, lease=expiry)
    assert rec.ordinal == 1  # legacy delivery_attempts is intentionally 9
    assert rec.execution_id == eid and rec.started_at == start and rec.ended_at is None
    assert rec.result is AttemptResult.RUNNING and rec.failure_code is None
    assert rec.lease_owner == "worker-1" and rec.lease_expires_at == expiry
    assert rec.trigger_kind is AttemptTriggerKind.RUN and rec.actor_user_id == "user-1"
    assert rec.recovery_source_attempt_id is None and rec.approval_id is None
    assert rec.reason_ref == "reason-1"
    with pytest.raises(AttributeError):
        rec.ordinal = 2  # type: ignore[misc]


async def test_open_close_history_and_close_preserves_lease(
    session: AsyncSession, executions: tuple[str, str]
) -> None:
    eid, _ = executions
    start = datetime(2026, 11, 25, 12, tzinfo=UTC)
    expiry = start + timedelta(minutes=4)
    rec = await _open(session, eid, started=start, lease=expiry)
    ended = start + timedelta(minutes=1)
    assert await repo.close_open(
        session,
        execution_id=eid,
        attempt_id=rec.id,
        result=AttemptResult.FAILED,
        failure_code="provider_error",
        ended_at=ended,
    )
    assert not await repo.close_open(
        session,
        execution_id=eid,
        attempt_id=rec.id,
        result=AttemptResult.FAILED,
        failure_code="provider_error",
        ended_at=ended,
    )
    closed = await repo.get(session, rec.id)
    assert closed is not None and closed.ended_at == ended
    assert closed.result is AttemptResult.FAILED and closed.failure_code == "provider_error"
    assert closed.lease_expires_at == expiry
    assert await repo.has_history(session, eid)
    rows, total = await repo.list_page(session, execution_id=eid, limit=10, offset=0)
    assert total == 1 and rows == [closed]


async def test_rollback_after_open_and_close_removes_history(
    session: AsyncSession, executions: tuple[str, str]
) -> None:
    eid, _ = executions
    start = datetime(2026, 11, 25, 12, tzinfo=UTC)
    rec = await _open(session, eid, started=start, lease=start + timedelta(minutes=2))
    await session.rollback()
    assert await repo.get(session, rec.id) is None
    assert not await repo.has_history(session, eid)


async def test_query_pages_filters_and_global_order(
    session: AsyncSession, executions: tuple[str, str]
) -> None:
    first, second = executions
    base = datetime(2026, 11, 25, 12, tzinfo=UTC)
    a = await _open(session, first, started=base, lease=base + timedelta(minutes=3))
    await repo.close_open(
        session,
        execution_id=first,
        attempt_id=a.id,
        result=AttemptResult.COMPLETED,
        failure_code=None,
        ended_at=base + timedelta(seconds=5),
    )
    b = await _open(
        session, first, started=base + timedelta(seconds=10), lease=base + timedelta(minutes=3)
    )
    c = await _open(
        session, second, started=base + timedelta(seconds=20), lease=base + timedelta(minutes=3)
    )
    page, total = await repo.list_page(session, execution_id=first, limit=1, offset=0)
    assert total == 2 and [r.id for r in page] == [a.id]
    page, total = await repo.list_page(session, execution_id=first, limit=1, offset=1)
    assert total == 2 and [r.id for r in page] == [b.id]
    page, total = await repo.list_page(session, execution_id=first, limit=2, offset=2)
    assert total == 2 and page == []
    assert await repo.has_history(session, second)
    operational, count = await repo.list_operational_page(
        session,
        result=AttemptResult.RUNNING,
        started_after=base + timedelta(seconds=5),
        started_before=base + timedelta(seconds=30),
        limit=10,
        offset=0,
    )
    assert count == 2 and [r.id for r in operational] == [c.id, b.id]
    operational, count = await repo.list_operational_page(
        session,
        result=AttemptResult.COMPLETED,
        started_after=None,
        started_before=None,
        limit=10,
        offset=0,
    )
    assert count == 1 and operational[0].id == a.id


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (1, -1)])
async def test_page_rejects_invalid_bounds(
    session: AsyncSession, executions: tuple[str, str], limit: int, offset: int
) -> None:
    with pytest.raises(ValueError):
        await repo.list_page(session, execution_id=executions[0], limit=limit, offset=offset)


async def test_close_rollback_restores_open_history(
    session: AsyncSession, executions: tuple[str, str]
) -> None:
    eid, _ = executions
    start = datetime(2026, 11, 25, 12, tzinfo=UTC)
    rec = await _open(session, eid, started=start, lease=start + timedelta(minutes=2))
    await session.flush()
    tx = await session.begin_nested()
    assert await repo.close_open(
        session,
        execution_id=eid,
        attempt_id=rec.id,
        result=AttemptResult.COMPLETED,
        failure_code=None,
        ended_at=start + timedelta(seconds=1),
    )
    await tx.rollback()
    restored = await repo.get(session, rec.id)
    assert restored is not None and restored.ended_at is None
    assert restored.result is AttemptResult.RUNNING
    assert await repo.has_history(session, eid)


async def test_failed_insert_can_be_rolled_back_without_history(
    session: AsyncSession, executions: tuple[str, str]
) -> None:
    from src.db.errors import ReferencedRowMissing

    eid, _ = executions
    start = datetime(2026, 11, 25, 12, tzinfo=UTC)
    with pytest.raises(ReferencedRowMissing):
        await repo.open_for_claim(
            session,
            execution_id=eid,
            started_at=start,
            lease_owner="worker-1",
            lease_expires_at=start + timedelta(minutes=2),
            trigger_kind=AttemptTriggerKind.RECOVERY,
            approval_id=None,
            actor_user_id=None,
            recovery_source_attempt_id="missing-attempt",
        )
    await session.rollback()
    assert not await repo.has_history(session, eid)
    assert (await session.execute(select(1))).scalar_one() == 1


async def test_operational_order_tie_break_and_page_boundaries(
    session: AsyncSession, executions: tuple[str, str]
) -> None:
    first, second = executions
    same = datetime(2026, 11, 25, 12, tzinfo=UTC)
    a = await _open(session, first, started=same, lease=same + timedelta(minutes=1))
    await repo.close_open(
        session,
        execution_id=first,
        attempt_id=a.id,
        result=AttemptResult.COMPLETED,
        failure_code=None,
        ended_at=same + timedelta(seconds=1),
    )
    b = await _open(session, second, started=same, lease=same + timedelta(minutes=1))
    await repo.close_open(
        session,
        execution_id=second,
        attempt_id=b.id,
        result=AttemptResult.FAILED,
        failure_code="failed",
        ended_at=same + timedelta(seconds=1),
    )
    rows, total = await repo.list_operational_page(
        session,
        result=None,
        started_after=same,
        started_before=same,
        limit=1,
        offset=0,
    )
    assert total == 2 and [r.id for r in rows] == [max(a.id, b.id)]
    rows, total = await repo.list_operational_page(
        session,
        result=None,
        started_after=same,
        started_before=same,
        limit=1,
        offset=1,
    )
    assert total == 2 and [r.id for r in rows] == [min(a.id, b.id)]
    rows, total = await repo.list_operational_page(
        session,
        result=None,
        started_after=same,
        started_before=same,
        limit=1,
        offset=2,
    )
    assert total == 2 and rows == []
