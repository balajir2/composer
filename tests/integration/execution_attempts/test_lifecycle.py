"""End-to-end attempt history across approval pause and resume."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Approval, ExecutionAttempt, WorkflowExecution
from src.db.repos import execution_attempts as attempts_repo
from src.db.repos import executions as executions_repo
from src.engine.langgraph_executor import LangGraphExecutor
from tests.integration.sqla_phase4a.conftest import (
    Db,
    EnqueueRecorder,
    ExecApi,
    Member,
    approval_graph,
    install_enqueue,
)

pytestmark = pytest.mark.integration


async def test_attempt_history_survives_pause_approval_resume_and_completion(
    client: AsyncClient,
    db: Db,
    member: Member,
    exec_api: ExecApi,
    sessionmaker_: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real graph run, in-app approval, and resumed graph form two immutable attempts."""
    nodes, edges = approval_graph()
    workflow_id = await db.seed_workflow(member, nodes=nodes, edges=edges)
    seen_at_graph_entry: list[tuple[str, int, str]] = []

    class ObservingExecutor(LangGraphExecutor):
        async def run(self, execution_id: str, *, attempt_id: str | None = None) -> None:
            assert attempt_id is not None
            async with sessionmaker_() as separate_session:
                attempt = await separate_session.get(ExecutionAttempt, attempt_id)
                execution = await separate_session.get(WorkflowExecution, execution_id)
                assert attempt is not None and execution is not None
                seen_at_graph_entry.append((attempt.id, attempt.ordinal, execution.status))
            await super().run(execution_id, attempt_id=attempt_id)

        async def resume(
            self, execution_id: str, decision: str, *, attempt_id: str | None = None
        ) -> None:
            assert attempt_id is not None
            async with sessionmaker_() as separate_session:
                attempt = await separate_session.get(ExecutionAttempt, attempt_id)
                execution = await separate_session.get(WorkflowExecution, execution_id)
                assert attempt is not None and execution is not None
                seen_at_graph_entry.append((attempt.id, attempt.ordinal, execution.status))
            await super().resume(execution_id, decision, attempt_id=attempt_id)

    monkeypatch.setattr("src.api.internal.LangGraphExecutor", ObservingExecutor)

    async def drive_after_commit(execution_id: str) -> None:
        response = await exec_api.claim(
            execution_id, "run" if not seen_at_graph_entry else "resume"
        )
        assert response.status_code == 200, response.text

    install_enqueue(monkeypatch, EnqueueRecorder(before=drive_after_commit))

    created = await exec_api.create(member, workflow_id, "payload")
    assert created.status_code == 202, created.text
    execution_id = created.json()["id"]

    async with sessionmaker_() as session:
        first_page, first_count = await attempts_repo.list_page(
            session, execution_id=execution_id, limit=10, offset=0
        )
        execution = await executions_repo.get(session, execution_id)
        assert execution is not None and execution.status == "waiting_approval"
    assert first_count == 1
    first = first_page[0]
    assert first.ordinal == 1 and first.result.value == "paused"
    assert first.approval_id is None
    first_snapshot = first

    approved = await client.post(
        f"/executions/{execution_id}/resume",
        json={"decision": "approved"},
        headers=member.headers,
    )
    assert approved.status_code == 200, approved.text

    async with sessionmaker_() as session:
        history, count = await attempts_repo.list_page(
            session, execution_id=execution_id, limit=10, offset=0
        )
        current = await session.get(WorkflowExecution, execution_id)
        approval = await session.scalar(
            select(Approval).where(Approval.execution_id == execution_id)
        )
    assert current is not None and current.status == "completed"
    assert count == 2 and [attempt.ordinal for attempt in history] == [1, 2]
    assert [attempt.result.value for attempt in history] == ["paused", "completed"]
    assert history[0].id == first.id and history[1].id != history[0].id
    assert history[1].trigger_kind.value == "resume"
    assert approval is not None and history[1].approval_id == approval.id
    assert history[0] == first_snapshot
    assert seen_at_graph_entry == [
        (history[0].id, 1, "running"),
        (history[1].id, 2, "running"),
    ]


async def test_history_availability_distinguishes_empty_history_from_missing_execution(
    session: AsyncSession,
) -> None:
    """A real execution can have no attempts; a missing execution is independently absent."""
    from src.db.models import Workflow

    workflow = Workflow(name="legacy history availability", nodes=[], edges=[])
    session.add(workflow)
    await session.flush()
    execution = WorkflowExecution(
        workflow_id=workflow.id,
        status="queued",
        node_results={},
        variables={},
        thread_id=f"legacy-{workflow.id}",
        delivery_attempts=9,
    )
    session.add(execution)
    await session.flush()

    assert await executions_repo.get(session, execution.id) is not None
    assert not await attempts_repo.has_history(session, execution.id)
    assert await executions_repo.get(session, "missing-execution") is None
    assert not await attempts_repo.has_history(session, "missing-execution")
    empty_page, empty_count = await attempts_repo.list_page(
        session, execution_id=execution.id, limit=10, offset=0
    )
    assert empty_page == [] and empty_count == 0

    now = datetime.now(UTC)
    claimed = await executions_repo.claim(
        session,
        execution.id,
        kind="run",
        worker_id="history-test-worker",
        now=now,
        lease_expires_at=now + timedelta(minutes=5),
    )
    assert claimed is not None
    assert await attempts_repo.has_history(session, execution.id)
    rows, count = await attempts_repo.list_page(
        session, execution_id=execution.id, limit=10, offset=0
    )
    assert count == 1 and [row.ordinal for row in rows] == [1]
