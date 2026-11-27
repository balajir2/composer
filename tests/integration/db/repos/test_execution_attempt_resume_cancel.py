"""Approval provenance and cancellation history across resume and claim boundaries."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Approval,
    ApprovalDecision,
    AttemptResult,
    AttemptTriggerKind,
    ExecutionAttempt,
    Workflow,
    WorkflowExecution,
)
from src.db.repos import approvals as approvals_repo
from src.db.repos import execution_attempts as attempts_repo
from src.db.repos import executions as executions_repo
from src.db.repos.execution_attempts import AttemptRecord

pytestmark = pytest.mark.integration


async def _execution(
    session: AsyncSession,
    *,
    status: str = "waiting_approval",
    variables: dict[str, str] | None = None,
) -> str:
    workflow = Workflow(name="resume cancel attempts", nodes=[], edges=[])
    session.add(workflow)
    await session.flush()
    row = WorkflowExecution(
        workflow_id=workflow.id,
        status=status,
        node_results={},
        variables=variables
        or {"_pending_approval_node": "gate", "_pending_approval_since": "pause-1"},
        thread_id=f"resume-cancel-{workflow.id}",
        delivery_attempts=0,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _open(session: AsyncSession, execution_id: str) -> AttemptRecord:
    now = datetime(2026, 11, 25, 12, tzinfo=UTC)
    return await attempts_repo.open_for_claim(
        session,
        execution_id=execution_id,
        started_at=now,
        lease_owner="worker",
        lease_expires_at=now + timedelta(minutes=5),
        trigger_kind=AttemptTriggerKind.RUN,
        approval_id=None,
        actor_user_id=None,
    )


async def test_resume_approval_id_bridges_to_the_later_resume_claim(session: AsyncSession) -> None:
    execution_id = await _execution(session)
    approval = await approvals_repo.create(
        session,
        execution_id=execution_id,
        node_id="gate",
        decision=ApprovalDecision.APPROVED,
        approver_user_id="reviewer",
        approver_email=None,
        via_email_link=False,
        note=None,
    )

    won = await executions_repo.resume_if_waiting(
        session,
        execution_id,
        decision="approved",
        now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        expected_pending=executions_repo.PendingInstance("gate", "pause-1"),
        approval_id=approval.id,
    )
    assert won
    row = await session.get(WorkflowExecution, execution_id, populate_existing=True)
    assert row is not None and row.pending_attempt_approval_id == approval.id

    claimed = await executions_repo.claim(
        session,
        execution_id,
        kind="resume",
        worker_id="worker",
        now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        lease_expires_at=datetime(2026, 11, 25, 12, 5, tzinfo=UTC),
    )
    assert claimed is not None
    attempt = await session.get(ExecutionAttempt, claimed.attempt_id)
    assert attempt is not None and attempt.approval_id == approval.id


async def test_approval_resolution_alone_does_not_open_an_attempt(session: AsyncSession) -> None:
    execution_id = await _execution(session)
    approval = await approvals_repo.create(
        session,
        execution_id=execution_id,
        node_id="gate",
        decision=ApprovalDecision.APPROVED,
        approver_user_id="reviewer",
        approver_email=None,
        via_email_link=False,
        note=None,
    )
    assert approval.id
    assert not list(
        await session.scalars(
            select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution_id)
        )
    )


async def test_losing_resume_rolls_back_its_new_approval(session: AsyncSession) -> None:
    execution_id = await _execution(session)
    nested = await session.begin_nested()
    approval = await approvals_repo.create(
        session,
        execution_id=execution_id,
        node_id="gate",
        decision=ApprovalDecision.APPROVED,
        approver_user_id="reviewer",
        approver_email=None,
        via_email_link=False,
        note=None,
    )
    assert not await executions_repo.resume_if_waiting(
        session,
        execution_id,
        decision="approved",
        now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        expected_pending=executions_repo.PendingInstance("gate", "obsolete-pause"),
        approval_id=approval.id,
    )
    await nested.rollback()
    assert await session.get(Approval, approval.id) is None


async def test_cancel_before_claim_creates_no_attempt(session: AsyncSession) -> None:
    execution_id = await _execution(session, status="queued", variables={})
    assert await executions_repo.cancel_if_active(
        session, execution_id, error="canceled", now=datetime(2026, 11, 25, 12, tzinfo=UTC)
    )
    assert (
        await executions_repo.claim(
            session,
            execution_id,
            kind="run",
            worker_id="worker",
            now=datetime(2026, 11, 25, 12, tzinfo=UTC),
            lease_expires_at=datetime(2026, 11, 25, 12, 5, tzinfo=UTC),
        )
        is None
    )
    assert not list(await session.scalars(select(ExecutionAttempt)))


async def test_claim_before_cancel_closes_attempt_as_canceled(session: AsyncSession) -> None:
    execution_id = await _execution(session, status="queued", variables={})
    claimed = await executions_repo.claim(
        session,
        execution_id,
        kind="run",
        worker_id="worker",
        now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        lease_expires_at=datetime(2026, 11, 25, 12, 5, tzinfo=UTC),
    )
    assert claimed is not None
    assert await executions_repo.cancel_if_active(
        session, execution_id, error="canceled", now=datetime(2026, 11, 25, 12, 1, tzinfo=UTC)
    )
    attempt = await session.get(ExecutionAttempt, claimed.attempt_id, populate_existing=True)
    assert attempt is not None
    assert AttemptResult(attempt.result) is AttemptResult.CANCELED
    assert attempt.ended_at == datetime(2026, 11, 25, 12, 1, tzinfo=UTC)


async def test_cancel_waiting_does_not_rewrite_closed_paused_attempt(session: AsyncSession) -> None:
    execution_id = await _execution(session, status="running", variables={})
    attempt = await _open(session, execution_id)
    assert await executions_repo.mark_waiting_approval_if_running(
        session,
        execution_id=execution_id,
        variables={"_pending_approval_node": "gate", "_pending_approval_since": "pause-1"},
        now=datetime(2026, 11, 25, 12, 1, tzinfo=UTC),
        attempt_id=attempt.id,
    )
    assert await executions_repo.cancel_if_active(
        session, execution_id, error="canceled", now=datetime(2026, 11, 25, 12, 2, tzinfo=UTC)
    )
    persisted = await session.get(ExecutionAttempt, attempt.id, populate_existing=True)
    assert persisted is not None and AttemptResult(persisted.result) is AttemptResult.PAUSED


async def test_cancel_before_pause_closes_attempt_and_pause_loses(session: AsyncSession) -> None:
    execution_id = await _execution(session, status="running", variables={})
    attempt = await _open(session, execution_id)
    assert await executions_repo.cancel_if_active(
        session, execution_id, error="canceled", now=datetime(2026, 11, 25, 12, 1, tzinfo=UTC)
    )
    assert not await executions_repo.mark_waiting_approval_if_running(
        session,
        execution_id=execution_id,
        variables={"_pending_approval_node": "gate"},
        now=datetime(2026, 11, 25, 12, 2, tzinfo=UTC),
        attempt_id=attempt.id,
    )
    persisted = await session.get(ExecutionAttempt, attempt.id, populate_existing=True)
    assert persisted is not None and AttemptResult(persisted.result) is AttemptResult.CANCELED


async def test_sequential_resume_then_cancel_without_claim_has_no_attempt(
    session: AsyncSession,
) -> None:
    execution_id = await _execution(session)
    assert await executions_repo.resume_if_waiting(
        session,
        execution_id,
        decision="approved",
        now=datetime(2026, 11, 25, 12, tzinfo=UTC),
        expected_pending=executions_repo.PendingInstance("gate", "pause-1"),
    )
    assert await executions_repo.cancel_if_active(
        session, execution_id, error="canceled", now=datetime(2026, 11, 25, 12, 1, tzinfo=UTC)
    )
    assert not list(
        await session.scalars(
            select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution_id)
        )
    )
