"""approvals repository: insert shape, FK failure, cascade (real Postgres)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import ReferencedRowMissing
from src.db.models import Approval, ApprovalDecision, Workflow, WorkflowExecution
from src.db.repos import approvals as repo

pytestmark = pytest.mark.integration


@pytest.fixture
async def execution_id(session: AsyncSession) -> AsyncIterator[str]:
    wf = Workflow(name="appr", nodes=[], edges=[])
    session.add(wf)
    await session.flush()
    ex = WorkflowExecution(
        workflow_id=wf.id,
        status="waiting_approval",
        node_results={},
        variables={},
        thread_id="th-appr",
    )
    session.add(ex)
    await session.flush()
    yield ex.id


async def test_create_returns_and_persists_every_column(
    session: AsyncSession, execution_id: str
) -> None:
    rec = await repo.create(
        session,
        execution_id=execution_id,
        node_id="n1",
        decision=ApprovalDecision.APPROVED,
        approver_user_id=None,
        approver_email="a@x.test",
        via_email_link=True,
        note=None,
    )
    assert rec.id and rec.decision is ApprovalDecision.APPROVED
    row = (await session.execute(select(Approval).where(Approval.id == rec.id))).scalar_one()
    stored: dict[str, Any] = {
        "execution_id": row.execution_id,
        "node_id": row.node_id,
        "approver_user_id": row.approver_user_id,
        "approver_email": row.approver_email,
        "via_email_link": row.via_email_link,
        "decision": ApprovalDecision(row.decision),
        "note": row.note,
        "created_at": row.created_at,
    }
    assert stored == {
        "execution_id": execution_id,
        "node_id": "n1",
        "approver_user_id": None,
        "approver_email": "a@x.test",
        "via_email_link": True,
        "decision": ApprovalDecision.APPROVED,
        "note": None,
        "created_at": rec.created_at,
    }
    assert rec.created_at is not None


async def test_in_app_decision_with_note(session: AsyncSession, execution_id: str) -> None:
    rec = await repo.create(
        session,
        execution_id=execution_id,
        node_id="n2",
        decision=ApprovalDecision.REJECTED,
        approver_user_id=None,
        approver_email="b@x.test",
        via_email_link=False,
        note="no thanks",
    )
    assert rec.via_email_link is False and rec.note == "no thanks"
    assert rec.decision is ApprovalDecision.REJECTED


async def test_missing_execution_raises_and_session_survives(session: AsyncSession) -> None:
    with pytest.raises(ReferencedRowMissing) as ei:
        await repo.create(
            session,
            execution_id="does-not-exist",
            node_id="n",
            decision=ApprovalDecision.APPROVED,
            approver_user_id=None,
            approver_email=None,
            via_email_link=False,
            note=None,
        )
    assert "approvals_execution_id_fkey" in str(ei.value)
    assert (await session.execute(select(1))).scalar_one() == 1


async def test_rows_cascade_with_the_execution(session: AsyncSession, execution_id: str) -> None:
    await repo.create(
        session,
        execution_id=execution_id,
        node_id="n",
        decision=ApprovalDecision.APPROVED,
        approver_user_id=None,
        approver_email=None,
        via_email_link=False,
        note=None,
    )
    await session.execute(delete(WorkflowExecution).where(WorkflowExecution.id == execution_id))
    left = (
        await session.execute(select(Approval).where(Approval.execution_id == execution_id))
    ).all()
    assert left == []
