"""Claiming work opens its durable attempt in the same transaction."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import (
    Approval,
    ApprovalDecision,
    AttemptResult,
    AttemptTriggerKind,
    ExecutionAttempt,
    Workflow,
    WorkflowExecution,
)
from src.db.repos import execution_attempts as attempts_repo
from src.db.repos import executions as repo

pytestmark = pytest.mark.integration


@pytest.fixture
async def workflow_id(session: AsyncSession) -> AsyncIterator[str]:
    workflow = Workflow(name="claim attempts", nodes=[], edges=[])
    session.add(workflow)
    await session.flush()
    yield workflow.id


async def _execution(
    session: AsyncSession,
    workflow_id: str,
    *,
    status: str = "queued",
    delivery_attempts: int = 0,
    variables: dict[str, str] | None = None,
) -> str:
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status=status,
        node_results={},
        variables=variables or {},
        thread_id=f"claim-{workflow_id}-{status}-{delivery_attempts}",
        delivery_attempts=delivery_attempts,
    )
    session.add(execution)
    await session.flush()
    return execution.id


async def _claim(
    session: AsyncSession, execution_id: str, *, kind: str = "run"
) -> repo.ClaimedExecution | None:
    now = datetime(2026, 11, 25, 12, tzinfo=UTC)
    return await repo.claim(
        session,
        execution_id,
        kind=kind,  # type: ignore[arg-type]
        worker_id="worker-test",
        now=now,
        lease_expires_at=now + timedelta(minutes=5),
    )


async def test_initial_claim_creates_first_run_attempt_even_when_delivery_count_is_nonzero(
    session: AsyncSession, workflow_id: str
) -> None:
    execution_id = await _execution(session, workflow_id, delivery_attempts=7)

    claimed = await _claim(session, execution_id)

    assert claimed is not None
    assert claimed.id == execution_id and claimed.variables == {}
    assert claimed.attempt_ordinal == 1 and claimed.attempt_id
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.id == claimed.attempt_id)
    )
    assert attempt is not None
    assert (
        AttemptTriggerKind(attempt.trigger_kind) is AttemptTriggerKind.RUN and attempt.ordinal == 1
    )
    row = await session.get(WorkflowExecution, execution_id)
    assert row is not None and row.delivery_attempts == 8


async def test_resume_claim_captures_and_clears_exact_pending_approval(
    session: AsyncSession, workflow_id: str
) -> None:
    execution_id = await _execution(
        session, workflow_id, status="running", variables={"_resume_decision": "approved"}
    )
    prior = await attempts_repo.open_for_claim(
        session,
        execution_id=execution_id,
        started_at=datetime(2026, 11, 25, 11, tzinfo=UTC),
        lease_owner="prior-worker",
        lease_expires_at=datetime(2026, 11, 25, 11, 5, tzinfo=UTC),
        trigger_kind=AttemptTriggerKind.RUN,
        approval_id=None,
        actor_user_id=None,
    )
    await attempts_repo.close_open(
        session,
        execution_id=execution_id,
        attempt_id=prior.id,
        result=AttemptResult.COMPLETED,
        failure_code=None,
        ended_at=datetime(2026, 11, 25, 11, 2, tzinfo=UTC),
    )
    approval = Approval(
        execution_id=execution_id,
        node_id="approval-node",
        decision=ApprovalDecision.APPROVED,
        approver_user_id="user-1",
        approver_email=None,
        via_email_link=False,
        note=None,
    )
    session.add(approval)
    await session.flush()
    await session.execute(
        update(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id)
        .values(pending_attempt_approval_id=approval.id)
    )

    claimed = await _claim(session, execution_id, kind="resume")

    assert claimed is not None and claimed.attempt_ordinal == 2
    attempt = await session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.id == claimed.attempt_id)
    )
    assert attempt is not None
    assert AttemptTriggerKind(attempt.trigger_kind) is AttemptTriggerKind.RESUME
    assert attempt.approval_id == approval.id
    row = await session.get(WorkflowExecution, execution_id)
    assert row is not None and row.pending_attempt_approval_id is None


@pytest.mark.parametrize(
    ("status", "kind", "lease_owner", "lease_expires_at"),
    [
        ("running", "run", None, None),
        ("queued", "resume", None, None),
        ("queued", "run", "locked", None),
        ("queued", "run", None, datetime(2026, 11, 26, tzinfo=UTC)),
        ("completed", "run", None, None),
        ("failed", "run", None, None),
        ("canceled", "run", None, None),
        ("waiting_approval", "run", None, None),
    ],
)
async def test_ineligible_claim_does_not_create_attempt(
    session: AsyncSession,
    workflow_id: str,
    status: str,
    kind: str,
    lease_owner: str | None,
    lease_expires_at: datetime | None,
) -> None:
    execution_id = await _execution(session, workflow_id, status=status)
    await session.execute(
        update(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id)
        .values(lease_owner=lease_owner, lease_expires_at=lease_expires_at)
    )

    claimed = await _claim(session, execution_id, kind=kind)

    assert claimed is None
    assert (
        await session.scalar(
            select(ExecutionAttempt.id).where(ExecutionAttempt.execution_id == execution_id)
        )
        is None
    )


async def test_missing_execution_claim_creates_no_attempt(session: AsyncSession) -> None:
    assert await _claim(session, "missing-execution") is None


async def test_claim_rollback_removes_lease_and_attempt(
    session: AsyncSession, workflow_id: str
) -> None:
    execution_id = await _execution(session, workflow_id)
    nested = await session.begin_nested()
    claimed = await _claim(session, execution_id)
    assert claimed is not None
    await nested.rollback()

    row = await session.get(WorkflowExecution, execution_id, populate_existing=True)
    assert row is not None and row.status == "queued" and row.delivery_attempts == 0
    assert row.lease_owner is None
    assert (
        await session.scalar(
            select(ExecutionAttempt.id).where(ExecutionAttempt.execution_id == execution_id)
        )
        is None
    )


@pytest.fixture
async def committed_workflow(
    engine: AsyncEngine,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], str]]:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        workflow = Workflow(name="concurrent claim attempts", nodes=[], edges=[])
        session.add(workflow)
        await session.commit()
        workflow_id = workflow.id
    try:
        yield sm, workflow_id
    finally:
        async with sm() as session:
            await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await session.commit()


@pytest.mark.parametrize("kind", ["run", "resume"])
async def test_five_independent_claim_sessions_open_exactly_one_attempt(
    committed_workflow: tuple[async_sessionmaker[AsyncSession], str], kind: str
) -> None:
    sm, workflow_id = committed_workflow
    async with sm() as session:
        execution = WorkflowExecution(
            workflow_id=workflow_id,
            status="queued" if kind == "run" else "running",
            node_results={},
            variables={"_resume_decision": "approved"},
            thread_id=f"concurrent-{kind}",
            delivery_attempts=0,
        )
        session.add(execution)
        await session.commit()
        execution_id = execution.id

    async def claim_one(index: int) -> repo.ClaimedExecution | None:
        async with sm() as session:
            result = await _claim(session, execution_id, kind=kind)
            await session.commit()
            return result

    results = await asyncio.gather(*(claim_one(i) for i in range(5)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].attempt_ordinal == 1
    async with sm() as session:
        attempts = list(
            await session.scalars(
                select(ExecutionAttempt).where(ExecutionAttempt.execution_id == execution_id)
            )
        )
        row = await session.get(WorkflowExecution, execution_id)
        assert len(attempts) == 1 and attempts[0].ordinal == 1
        assert row is not None and row.delivery_attempts == 1
