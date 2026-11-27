"""Reconciliation closes open attempts whose execution is already terminal.

Models the rollback to the 0004 image: old code cancels or lease-fails an execution without
touching attempt history, leaving the attempt open forever.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AttemptResult, Workflow, WorkflowExecution
from src.db.repos import execution_attempts as attempts_repo
from src.db.repos import executions as repo

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
STARTED = NOW - timedelta(minutes=10)


@pytest.fixture
async def workflow(session: AsyncSession) -> AsyncIterator[str]:
    row = Workflow(name="reconcile attempts", nodes=[], edges=[])
    session.add(row)
    await session.flush()
    yield row.id


async def _open_attempt(session: AsyncSession, workflow_id: str, tag: str) -> tuple[str, str]:
    row = WorkflowExecution(
        workflow_id=workflow_id,
        status="queued",
        node_results={},
        variables={},
        thread_id=f"reconcile-{workflow_id}-{tag}",
        delivery_attempts=0,
    )
    session.add(row)
    await session.flush()
    claimed = await repo.claim(
        session,
        row.id,
        kind="run",
        worker_id="reconcile-worker",
        now=STARTED,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed is not None
    return row.id, claimed.attempt_id


async def _old_code_ends(
    session: AsyncSession, execution_id: str, status: str, completed_at: datetime | None
) -> None:
    """What the 0004 image did: flip the summary only."""
    await session.execute(
        update(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id)
        .values(status=status, completed_at=completed_at)
    )


@pytest.mark.parametrize(
    ("status", "result", "completed_at", "expected_end"),
    [
        ("failed", AttemptResult.FAILED, NOW - timedelta(minutes=3), NOW - timedelta(minutes=3)),
        (
            "canceled",
            AttemptResult.CANCELED,
            NOW - timedelta(minutes=2),
            NOW - timedelta(minutes=2),
        ),
        ("completed", AttemptResult.COMPLETED, None, NOW),
    ],
)
async def test_terminal_execution_open_attempt_is_closed(
    session: AsyncSession,
    workflow: str,
    status: str,
    result: AttemptResult,
    completed_at: datetime | None,
    expected_end: datetime,
) -> None:
    execution_id, attempt_id = await _open_attempt(session, workflow, status)
    await _old_code_ends(session, execution_id, status, completed_at)

    closed = await attempts_repo.close_open_for_terminal_executions(session, now=NOW)

    assert closed >= 1
    attempt = await attempts_repo.get(session, attempt_id)
    assert attempt is not None
    assert (attempt.result, attempt.failure_code, attempt.ended_at) == (
        result,
        "closed_by_reconciliation",
        expected_end,
    )


async def test_end_time_never_precedes_the_attempt_start(
    session: AsyncSession, workflow: str
) -> None:
    execution_id, attempt_id = await _open_attempt(session, workflow, "early")
    await _old_code_ends(session, execution_id, "failed", STARTED - timedelta(hours=1))

    await attempts_repo.close_open_for_terminal_executions(session, now=NOW)

    attempt = await attempts_repo.get(session, attempt_id)
    assert attempt is not None and attempt.ended_at == STARTED


@pytest.mark.parametrize("status", ["running", "waiting_approval", "queued"])
async def test_non_terminal_execution_keeps_its_open_attempt(
    session: AsyncSession, workflow: str, status: str
) -> None:
    execution_id, attempt_id = await _open_attempt(session, workflow, status)
    await _old_code_ends(session, execution_id, status, None)

    await attempts_repo.close_open_for_terminal_executions(session, now=NOW)

    attempt = await attempts_repo.get(session, attempt_id)
    assert attempt is not None
    assert (attempt.result, attempt.failure_code, attempt.ended_at) == (
        AttemptResult.RUNNING,
        None,
        None,
    )


async def test_rerun_is_a_no_op(session: AsyncSession, workflow: str) -> None:
    execution_id, attempt_id = await _open_attempt(session, workflow, "twice")
    await _old_code_ends(session, execution_id, "failed", NOW - timedelta(minutes=3))

    first = await attempts_repo.close_open_for_terminal_executions(session, now=NOW)
    before = await attempts_repo.get(session, attempt_id)
    second = await attempts_repo.close_open_for_terminal_executions(
        session, now=NOW + timedelta(hours=1)
    )
    after = await attempts_repo.get(session, attempt_id)

    assert first >= 1
    assert second == 0
    assert before == after
