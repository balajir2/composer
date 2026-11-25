"""Worker outcomes are fenced to their own open attempt."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import AttemptResult, Workflow, WorkflowExecution
from src.db.repos import execution_attempts as attempts_repo
from src.db.repos import executions as repo

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
END = NOW + timedelta(minutes=1)


async def _seed(session: AsyncSession) -> str:
    workflow = Workflow(name="attempt transition", nodes=[], edges=[])
    session.add(workflow)
    await session.flush()
    execution = WorkflowExecution(
        workflow_id=workflow.id,
        status="queued",
        node_results={},
        variables={},
        thread_id=f"attempt-transition-{workflow.id}",
        delivery_attempts=0,
    )
    session.add(execution)
    await session.flush()
    return execution.id


async def _claim(session: AsyncSession, execution_id: str, kind: str = "run") -> str:
    claimed = await repo.claim(
        session,
        execution_id,
        kind=kind,  # type: ignore[arg-type]
        worker_id="worker",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert claimed is not None
    return claimed.attempt_id


async def _transition(
    session: AsyncSession, execution_id: str, attempt_id: str | None, outcome: str
) -> bool:
    if outcome == "completed":
        return await repo.mark_completed_if_running(
            session,
            execution_id,
            attempt_id=attempt_id,
            output={"answer": 42},
            variables={"done": True},
            node_results={"end": 42},
            now=END,
        )
    if outcome == "failed":
        return await repo.mark_failed_if_running(
            session,
            execution_id,
            attempt_id=attempt_id,
            error="RuntimeError: boom",
            failure_code="execution_error",
            now=END,
        )
    return await repo.mark_waiting_approval_if_running(
        session, execution_id, attempt_id=attempt_id, variables={"pending": True}, now=END
    )


@pytest.mark.parametrize(
    ("outcome", "result"),
    [
        ("completed", AttemptResult.COMPLETED),
        ("failed", AttemptResult.FAILED),
        ("paused", AttemptResult.PAUSED),
    ],
)
async def test_worker_outcome_closes_exact_attempt_once(
    session: AsyncSession, outcome: str, result: AttemptResult
) -> None:
    execution_id = await _seed(session)
    attempt_id = await _claim(session, execution_id)
    assert await _transition(session, execution_id, attempt_id, outcome)
    attempt = await attempts_repo.get(session, attempt_id)
    assert attempt is not None
    assert (attempt.result, attempt.ended_at, attempt.failure_code) == (
        result,
        END,
        "execution_error" if outcome == "failed" else None,
    )
    summary = await repo.get(session, execution_id)
    assert summary is not None
    assert summary.status == ("waiting_approval" if outcome == "paused" else outcome)
    assert summary.completed_at == (None if outcome == "paused" else END)
    assert summary.failure_code == ("execution_error" if outcome == "failed" else None)
    before: tuple[Any, ...] = (attempt, summary)
    assert not await _transition(session, execution_id, attempt_id, outcome)
    assert (
        await attempts_repo.get(session, attempt_id),
        await repo.get(session, execution_id),
    ) == before


@pytest.mark.parametrize("outcome", ["completed", "failed", "paused"])
async def test_stale_attempt_cannot_change_new_open_attempt(
    session: AsyncSession, outcome: str
) -> None:
    execution_id = await _seed(session)
    old_id = await _claim(session, execution_id)
    assert await _transition(session, execution_id, old_id, "paused")
    resumed = await repo.resume_if_waiting(session, execution_id, decision="approved", now=END)
    assert resumed
    new_id = await _claim(session, execution_id, "resume")
    before = (await repo.get(session, execution_id), await attempts_repo.get(session, new_id))
    assert not await _transition(session, execution_id, old_id, outcome)
    assert (
        await repo.get(session, execution_id),
        await attempts_repo.get(session, new_id),
    ) == before
    assert await _transition(session, execution_id, new_id, outcome)


@pytest.mark.parametrize("outcome", ["completed", "failed", "paused"])
async def test_missing_identity_loses_when_open_attempt_exists(
    session: AsyncSession, outcome: str
) -> None:
    execution_id = await _seed(session)
    attempt_id = await _claim(session, execution_id)
    before = (await repo.get(session, execution_id), await attempts_repo.get(session, attempt_id))
    assert not await _transition(session, execution_id, None, outcome)
    assert (
        await repo.get(session, execution_id),
        await attempts_repo.get(session, attempt_id),
    ) == before


async def test_transition_rollback_restores_summary_and_open_attempt(session: AsyncSession) -> None:
    execution_id = await _seed(session)
    attempt_id = await _claim(session, execution_id)
    nested = await session.begin_nested()
    assert await _transition(session, execution_id, attempt_id, "completed")
    await nested.rollback()
    summary = await repo.get(session, execution_id)
    attempt = await attempts_repo.get(session, attempt_id)
    assert summary is not None and summary.status == "running"
    assert attempt is not None and attempt.result == AttemptResult.RUNNING
    assert attempt.ended_at is None


async def test_unexpected_close_loss_raises_and_rollback_restores_summary(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id = await _seed(session)
    attempt_id = await _claim(session, execution_id)

    async def lose_close(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(attempts_repo, "close_open", lose_close)
    nested = await session.begin_nested()
    with pytest.raises(RuntimeError, match="attempt closure lost"):
        await _transition(session, execution_id, attempt_id, "completed")
    await nested.rollback()
    summary = await repo.get(session, execution_id)
    attempt = await attempts_repo.get(session, attempt_id)
    assert summary is not None and summary.status == "running"
    assert attempt is not None and attempt.ended_at is None


async def test_legacy_running_row_without_open_attempt_can_transition(
    session: AsyncSession,
) -> None:
    execution_id = await _seed(session)
    await session.execute(
        update(WorkflowExecution)
        .where(WorkflowExecution.id == execution_id)
        .values(status="running")
    )
    assert await _transition(session, execution_id, None, "completed")
    summary = await repo.get(session, execution_id)
    assert summary is not None and summary.status == "completed"


async def test_legacy_outcome_waits_for_concurrent_claim_then_loses(
    engine: AsyncEngine,
) -> None:
    """A None-ID outcome must see an attempt committed while it waits on the parent row."""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as setup:
        workflow = Workflow(name="legacy transition race", nodes=[], edges=[])
        setup.add(workflow)
        await setup.flush()
        execution = WorkflowExecution(
            workflow_id=workflow.id,
            status="running",
            node_results={},
            variables={},
            thread_id=f"legacy-transition-{uuid4()}",
            delivery_attempts=0,
        )
        setup.add(execution)
        await setup.commit()
        workflow_id, execution_id = workflow.id, execution.id

    outcome_pid: int | None = None
    outcome_task: asyncio.Task[bool] | None = None
    outcome_started = asyncio.Event()
    try:
        async with sm() as claim_session:
            claimed = await repo.claim(
                claim_session,
                execution_id,
                kind="resume",
                worker_id="claimant",
                now=NOW,
                lease_expires_at=NOW + timedelta(minutes=5),
            )
            assert claimed is not None

            async def legacy_outcome() -> bool:
                nonlocal outcome_pid
                async with sm() as legacy_session:
                    outcome_pid = await legacy_session.scalar(text("SELECT pg_backend_pid()"))
                    outcome_started.set()
                    won = await repo.mark_completed_if_running(
                        legacy_session,
                        execution_id,
                        attempt_id=None,
                        output={"legacy": True},
                        variables={},
                        node_results={},
                        now=END,
                    )
                    await legacy_session.commit()
                    return won

            outcome_task = asyncio.create_task(legacy_outcome())
            await outcome_started.wait()

            async def outcome_is_waiting_on_lock() -> bool:
                async with sm() as observer:
                    return bool(
                        await observer.scalar(
                            text(
                                "SELECT wait_event_type = 'Lock' "
                                "FROM pg_stat_activity WHERE pid = :pid"
                            ),
                            {"pid": outcome_pid},
                        )
                    )

            async with asyncio.timeout(5):
                while not await outcome_is_waiting_on_lock():
                    await asyncio.sleep(0.01)

            await claim_session.commit()
            assert not await outcome_task

        async with sm() as verify:
            summary = await repo.get(verify, execution_id)
            attempt = await attempts_repo.get(verify, claimed.attempt_id)
            assert summary is not None
            assert summary.status == "running" and summary.lease_owner == "claimant"
            assert attempt is not None and attempt.result == AttemptResult.RUNNING
            assert attempt.ended_at is None
    finally:
        if outcome_task is not None and not outcome_task.done():
            outcome_task.cancel()
            await asyncio.gather(outcome_task, return_exceptions=True)
        async with sm() as cleanup:
            await cleanup.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await cleanup.commit()
