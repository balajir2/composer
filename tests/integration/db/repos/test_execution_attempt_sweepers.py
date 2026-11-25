"""Sweeper transitions close only attempt history they actually supersede."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AttemptResult, ExecutionAttempt, Workflow, WorkflowExecution
from src.db.repos import execution_attempts as attempts_repo
from src.db.repos import executions as repo

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


@pytest.fixture
async def workflow(session: AsyncSession) -> AsyncIterator[str]:
    row = Workflow(name="sweeper attempts", nodes=[], edges=[])
    session.add(row)
    await session.flush()
    yield row.id


async def _execution(
    session: AsyncSession, workflow_id: str, *, status: str, variables: dict[str, str] | None = None
) -> str:
    row = WorkflowExecution(
        workflow_id=workflow_id,
        status=status,
        node_results={},
        variables=variables or {},
        thread_id=f"sweeper-{workflow_id}-{status}",
        delivery_attempts=0,
        running_since=NOW - timedelta(minutes=10) if status == "running" else None,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _attempt(session: AsyncSession, execution_id: str) -> str:
    status = await repo.get_status(session, execution_id)
    claimed = await repo.claim(
        session,
        execution_id,
        kind="run" if status == "queued" else "resume",
        worker_id="sweeper-worker",
        now=NOW - timedelta(minutes=10),
        lease_expires_at=NOW - timedelta(minutes=1),
    )
    assert claimed is not None
    return claimed.attempt_id


async def _only_attempt(session: AsyncSession, execution_id: str):
    return await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution_id)
    )


async def test_expired_lease_failure_closes_matching_open_attempt(
    session: AsyncSession, workflow: str
) -> None:
    execution_id = await _execution(session, workflow, status="queued")
    attempt_id = await _attempt(session, execution_id)

    assert await repo.fail_expired_lease_if_unchanged(
        session,
        execution_id,
        lease_expires_at=NOW - timedelta(minutes=1),
        error="expired",
        now=NOW,
    )

    attempt = await attempts_repo.get(session, attempt_id)
    summary = await repo.get(session, execution_id)
    assert attempt is not None
    assert (attempt.result, attempt.failure_code, attempt.ended_at) == (
        AttemptResult.FAILED,
        "lease_expired",
        NOW,
    )
    assert summary is not None and summary.status == "failed"
    assert summary.failure_code == "lease_expired"


async def test_stale_lease_guard_does_not_close_attempt_or_change_summary(
    session: AsyncSession, workflow: str
) -> None:
    execution_id = await _execution(session, workflow, status="queued")
    attempt_id = await _attempt(session, execution_id)
    newer_lease = NOW + timedelta(minutes=5)
    await session.execute(
        update(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id)
        .values(lease_expires_at=newer_lease)
    )
    before = await repo.get(session, execution_id)

    assert not await repo.fail_expired_lease_if_unchanged(
        session,
        execution_id,
        lease_expires_at=NOW - timedelta(minutes=1),
        error="expired",
        now=NOW,
    )

    attempt = await attempts_repo.get(session, attempt_id)
    assert attempt is not None and attempt.result is AttemptResult.RUNNING
    assert attempt.ended_at is None
    assert await repo.get(session, execution_id) == before


async def test_orphaned_unclaimed_resume_fails_without_attempt(
    session: AsyncSession, workflow: str
) -> None:
    execution_id = await _execution(session, workflow, status="running")

    assert await repo.fail_orphaned_if_unchanged(
        session,
        execution_id,
        cutoff=NOW - timedelta(minutes=1),
        error="orphaned",
        now=NOW,
    )

    summary = await repo.get(session, execution_id)
    assert summary is not None and summary.status == "failed"
    assert summary.failure_code == "orphaned_running"
    assert await _only_attempt(session, execution_id) is None


async def test_stale_orphan_snapshot_after_claim_does_not_fail_or_close(
    session: AsyncSession, workflow: str
) -> None:
    execution_id = await _execution(session, workflow, status="running")
    attempt_id = await _attempt(session, execution_id)
    before = await repo.get(session, execution_id)

    assert not await repo.fail_orphaned_if_unchanged(
        session,
        execution_id,
        cutoff=NOW - timedelta(minutes=1),
        error="orphaned",
        now=NOW,
    )

    assert await repo.get(session, execution_id) == before
    attempt = await attempts_repo.get(session, attempt_id)
    assert attempt is not None and attempt.result is AttemptResult.RUNNING
    assert attempt.ended_at is None


async def test_approval_expiry_after_pause_preserves_paused_attempt(
    session: AsyncSession, workflow: str
) -> None:
    execution_id = await _execution(session, workflow, status="queued")
    attempt_id = await _attempt(session, execution_id)
    assert await repo.mark_waiting_approval_if_running(
        session,
        execution_id,
        attempt_id=attempt_id,
        variables={"_pending_approval_since": (NOW - timedelta(days=9)).isoformat()},
        now=NOW - timedelta(minutes=5),
    )
    snapshot = {"_pending_approval_since": (NOW - timedelta(days=9)).isoformat()}

    assert await repo.fail_expired_approval_if_unchanged(
        session,
        execution_id,
        variables_snapshot=snapshot,
        error="approval expired",
        now=NOW,
    )

    attempt = await attempts_repo.get(session, attempt_id)
    summary = await repo.get(session, execution_id)
    assert attempt is not None and attempt.result is AttemptResult.PAUSED
    assert attempt.ended_at == NOW - timedelta(minutes=5)
    assert summary is not None and summary.status == "failed"
    assert (
        await session.scalar(
            select(ExecutionAttempt.id).where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionAttempt.ended_at.is_(None),
            )
        )
        is None
    )


async def test_stale_approval_snapshot_after_repause_does_not_close_new_pause(
    session: AsyncSession, workflow: str
) -> None:
    old = {"_pending_approval_since": (NOW - timedelta(days=9)).isoformat()}
    execution_id = await _execution(session, workflow, status="waiting_approval", variables=old)
    new = {"_pending_approval_since": (NOW - timedelta(days=8)).isoformat()}
    assert await repo.resume_if_waiting(session, execution_id, decision="approved", now=NOW)
    attempt_id = await _attempt(session, execution_id)
    assert await repo.mark_waiting_approval_if_running(
        session,
        execution_id,
        attempt_id=attempt_id,
        variables=new,
        now=NOW - timedelta(minutes=1),
    )
    before = await repo.get(session, execution_id)

    assert not await repo.fail_expired_approval_if_unchanged(
        session, execution_id, variables_snapshot=old, error="expired", now=NOW
    )

    assert await repo.get(session, execution_id) == before
    attempt = await attempts_repo.get(session, attempt_id)
    assert attempt is not None and attempt.result is AttemptResult.PAUSED
    assert attempt.ended_at == NOW - timedelta(minutes=1)


async def test_legacy_orphan_without_attempt_fails_without_fabricating_history(
    session: AsyncSession, workflow: str
) -> None:
    execution_id = await _execution(session, workflow, status="running")

    assert await repo.fail_orphaned_if_unchanged(
        session,
        execution_id,
        cutoff=NOW - timedelta(minutes=1),
        error="legacy orphan",
        now=NOW,
    )

    summary = await repo.get(session, execution_id)
    assert summary is not None and summary.status == "failed"
    assert summary.failure_code == "orphaned_running"
    assert await _only_attempt(session, execution_id) is None
