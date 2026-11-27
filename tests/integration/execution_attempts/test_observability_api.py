"""API authorization and empty-history behavior for observability reads."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import (
    Approval,
    ApprovalDecision,
    AttemptResult,
    AttemptTriggerKind,
    ExecutionAttempt,
    User,
    UserRole,
)
from tests.integration.sqla_phase4a.conftest import Db, ExecApi, MakeUser, Member

pytestmark = pytest.mark.integration


async def test_owner_legacy_history_and_page_validation(
    db: Db, member: Member, exec_api: ExecApi
) -> None:
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(workflow_id, member)
    summary = await exec_api.client.get(
        f"/executions/{execution_id}/observability", headers=member.headers
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["historyAvailable"] is False
    attempts = await exec_api.client.get(
        f"/executions/{execution_id}/attempts", headers=member.headers
    )
    assert attempts.json() == {
        "historyAvailable": False,
        "total": 0,
        "limit": 50,
        "offset": 0,
        "items": [],
    }
    for query in ("limit=0", "limit=101", "offset=-1"):
        response = await exec_api.client.get(
            f"/executions/{execution_id}/attempts?{query}", headers=member.headers
        )
        assert response.status_code == 422


async def test_unrelated_member_cannot_read_observability(
    db: Db, member: Member, make_user: MakeUser, exec_api: ExecApi
) -> None:
    second_member = await make_user()
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(workflow_id, member)
    response = await exec_api.client.get(
        f"/executions/{execution_id}/observability", headers=second_member.headers
    )
    assert response.status_code == 404


async def test_exact_runner_can_read_summary_and_attempts(
    db: Db, member: Member, exec_api: ExecApi, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(workflow_id, member, status="completed")
    started = datetime.now(UTC)
    async with sessionmaker_() as session:
        approval = Approval(
            execution_id=execution_id,
            node_id="approval-node",
            approver_user_id=member.id,
            decision=ApprovalDecision.APPROVED,
        )
        session.add(approval)
        await session.flush()
        source = ExecutionAttempt(
            execution_id=execution_id,
            ordinal=1,
            started_at=started,
            ended_at=started,
            result=AttemptResult.FAILED,
            failure_code="execution_error",
            lease_owner="source-lease-secret",
            lease_expires_at=started + timedelta(minutes=1),
            trigger_kind=AttemptTriggerKind.RUN,
        )
        session.add(source)
        await session.flush()
        session.add(
            ExecutionAttempt(
                execution_id=execution_id,
                ordinal=2,
                started_at=started + timedelta(seconds=1),
                ended_at=started + timedelta(seconds=2),
                result=AttemptResult.FAILED,
                failure_code="execution_error",
                lease_owner="lease-owner-secret",
                lease_expires_at=started + timedelta(minutes=2),
                trigger_kind=AttemptTriggerKind.RECOVERY,
                recovery_source_attempt_id=source.id,
                approval_id=approval.id,
                actor_user_id=member.id,
                reason_ref="reason-secret",
            )
        )
        await session.commit()
    summary = await exec_api.client.get(
        f"/executions/{execution_id}/observability", headers=member.headers
    )
    attempts = await exec_api.client.get(
        f"/executions/{execution_id}/attempts", headers=member.headers
    )
    assert summary.status_code == attempts.status_code == 200
    assert "adminDiagnostics" not in summary.json()
    item = attempts.json()["items"][1]
    assert set(item) == {
        "id",
        "ordinal",
        "startedAt",
        "endedAt",
        "result",
        "triggerKind",
        "failureGuidance",
        "hasApproval",
    }
    assert item["hasApproval"] is True
    assert item["failureGuidance"] == (
        "The execution failed. Contact an administrator if the problem continues."
    )
    assert all(
        secret not in str(attempts.json())
        for secret in (
            "lease-owner-secret",
            "source-lease-secret",
            "reason-secret",
            approval.id,
        )
    )


async def test_admin_receives_diagnostics_and_admin_attempt_fields(
    db: Db,
    member: Member,
    make_user: MakeUser,
    exec_api: ExecApi,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    admin = await make_user()
    async with sessionmaker_() as session:
        await session.execute(update(User).where(User.id == admin.id).values(role=UserRole.ADMIN))
        await session.commit()
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(
        workflow_id, member, status="failed", failure_code="node_error"
    )
    started = datetime.now(UTC)
    async with sessionmaker_() as session:
        session.add(
            ExecutionAttempt(
                execution_id=execution_id,
                ordinal=1,
                started_at=started,
                ended_at=started,
                result=AttemptResult.FAILED,
                failure_code="node_error",
                lease_owner="worker-1",
                lease_expires_at=started + timedelta(minutes=1),
                trigger_kind=AttemptTriggerKind.RUN,
                actor_user_id=member.id,
                reason_ref="reason-internal",
            )
        )
        await session.commit()
    summary = await exec_api.client.get(
        f"/executions/{execution_id}/observability", headers=admin.headers
    )
    attempts = await exec_api.client.get(
        f"/executions/{execution_id}/attempts", headers=admin.headers
    )
    assert summary.status_code == attempts.status_code == 200
    assert summary.json()["adminDiagnostics"]["threadId"]
    item = attempts.json()["items"][0]
    assert item["leaseOwner"] == "worker-1"
    assert item["failureCode"] == "node_error"
    assert item["actorUserId"] == member.id
    assert item["reasonRef"] == "reason-internal"


async def test_workflow_assignee_cannot_read_other_runners_execution(
    db: Db, member: Member, make_user: MakeUser, exec_api: ExecApi
) -> None:
    second_member = await make_user()
    workflow_id = await db.seed_workflow(member)
    await db.assign(workflow_id, second_member.id, member.id)
    execution_id = await db.seed_execution(workflow_id, member)
    for endpoint in ("observability", "attempts"):
        response = await exec_api.client.get(
            f"/executions/{execution_id}/{endpoint}", headers=second_member.headers
        )
        assert response.status_code == 404


async def test_inactive_user_is_rejected_by_auth_dependency(
    db: Db, member: Member, exec_api: ExecApi, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(workflow_id, member)
    async with sessionmaker_() as session:
        await session.execute(update(User).where(User.id == member.id).values(is_active=False))
        await session.commit()
    response = await exec_api.client.get(
        f"/executions/{execution_id}/observability", headers=member.headers
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "account is deactivated"}


async def test_missing_execution_returns_404(db: Db, member: Member, exec_api: ExecApi) -> None:
    response = await exec_api.client.get(
        "/executions/missing-observability/observability", headers=member.headers
    )
    assert response.status_code == 404


async def test_attempt_http_page_is_ordered_by_ordinal(
    db: Db, member: Member, exec_api: ExecApi, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    workflow_id = await db.seed_workflow(member)
    execution_id = await db.seed_execution(workflow_id, member)
    now = datetime.now(UTC)
    async with sessionmaker_() as session:
        for ordinal in (3, 1, 2):
            started = now + timedelta(seconds=ordinal)
            session.add(
                ExecutionAttempt(
                    execution_id=execution_id,
                    ordinal=ordinal,
                    started_at=started,
                    ended_at=started,
                    result=AttemptResult.COMPLETED,
                    lease_owner=f"worker-{ordinal}",
                    lease_expires_at=started + timedelta(minutes=1),
                    trigger_kind=AttemptTriggerKind.RUN,
                )
            )
        await session.commit()
    response = await exec_api.client.get(
        f"/executions/{execution_id}/attempts?limit=2&offset=0", headers=member.headers
    )
    assert response.status_code == 200
    assert [item["ordinal"] for item in response.json()["items"]] == [1, 2]
    assert response.json()["total"] == 3
