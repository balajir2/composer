import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    ApiKey,
    Approval,
    ApprovalDecision,
    CloudStorageConnection,
    DeploymentSetting,
    ExecutionEvent,
    LlmApiKey,
    LlmModel,
    McpOAuthState,
    McpOAuthToken,
    McpServer,
    RateLimitBucket,
    SaverCheckpointBlob,
    SaverCheckpointWrite,
    User,
    UserRole,
    Workflow,
    WorkflowAssignment,
    WorkflowExecution,
)

pytestmark = pytest.mark.integration


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def test_workflow_execution_approval_and_event_round_trip(session: AsyncSession) -> None:
    workflow = Workflow(name="round trip", tags=["a", "b"], nodes=[{"id": "start"}], edges=[])
    u1, u2 = _unique("u"), _unique("u")
    session.add_all(
        [
            workflow,
            User(id=u1, email=f"{u1}@example.com", role=UserRole.MEMBER),
            User(id=u2, email=f"{u2}@example.com", role=UserRole.MEMBER),
        ]
    )
    await session.flush()
    execution = WorkflowExecution(
        workflow_id=workflow.id,
        status="queued",
        node_results={"start": {"ok": True}},
        variables={},
        thread_id=_unique("thread"),
    )
    session.add(execution)
    await session.flush()
    session.add_all(
        [
            Approval(
                execution_id=execution.id, node_id="approve", decision=ApprovalDecision.APPROVED
            ),
            ExecutionEvent(execution_id=execution.id, seq=1, type="started", payload={"n": 1}),
            WorkflowAssignment(workflow_id=workflow.id, user_id=u1, assigned_by_id=u2),
        ]
    )
    await session.flush()
    session.expunge_all()

    loaded = (
        await session.execute(select(Workflow).where(Workflow.id == workflow.id))
    ).scalar_one()
    assert loaded.tags == ["a", "b"]
    assert loaded.nodes == [{"id": "start"}]
    assert loaded.is_template is False and loaded.is_production is False
    assert loaded.created_at.tzinfo is UTC and loaded.updated_at.tzinfo is UTC

    run = (
        await session.execute(select(WorkflowExecution).where(WorkflowExecution.id == execution.id))
    ).scalar_one()
    assert run.node_results == {"start": {"ok": True}}
    assert run.delivery_attempts == 0
    assert uuid.UUID(run.id).version == 7

    approval = (
        await session.execute(select(Approval).where(Approval.execution_id == execution.id))
    ).scalar_one()
    assert approval.decision is ApprovalDecision.APPROVED
    assert approval.via_email_link is False


async def test_users_keys_and_enum_default(session: AsyncSession) -> None:
    user = User(email=f"{_unique('user')}@example.com")
    session.add(user)
    await session.flush()
    session.add(ApiKey(user_id=user.id, label="ci", key_hash=_unique("hash"), key_prefix="ck_"))
    await session.flush()
    session.expunge_all()
    loaded = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert loaded.role is UserRole.MEMBER
    assert loaded.is_active is True and loaded.must_change_password is False


async def test_bytes_text_defaults_and_double_precision(session: AsyncSession) -> None:
    thread_id = _unique("thread")
    now = datetime.now(UTC)
    server_row = McpServer(name=_unique("mcp"), url="https://example.com", auth_type="none")
    bucket_row = RateLimitBucket(
        route_key=_unique("route"), client_key=_unique("c"), tokens=2.5, last_refill=now
    )
    session.add_all(
        [
            SaverCheckpointBlob(
                thread_id=thread_id, channel="x", version="1", type="bytes", blob=b"\x00\x01"
            ),
            SaverCheckpointWrite(
                thread_id=thread_id,
                checkpoint_id="c1",
                task_id="t",
                idx=0,
                channel="x",
                blob=b"\xff",
            ),
            server_row,
            McpOAuthToken(mcp_server_id="s", user_id=_unique("u"), encrypted_access_token="enc"),
            McpOAuthState(
                mcp_server_id="s",
                user_id="u",
                state=_unique("state"),
                code_verifier="v",
                redirect_uri="https://example.com/cb",
                expires_at=now + timedelta(minutes=5),
            ),
            CloudStorageConnection(
                user_id=_unique("u"),
                provider="google-drive",
                account_email="a@example.com",
                encrypted_access_token="enc",
            ),
            LlmApiKey(provider=_unique("provider"), encrypted_key="enc", key_prefix="sk-"),
            LlmModel(provider="anthropic", model_id=_unique("model")),
            DeploymentSetting(key=_unique("setting"), value="on"),
            bucket_row,
        ]
    )
    await session.flush()
    server_id, bucket_id = server_row.id, bucket_row.id
    session.expunge_all()

    blob = (
        await session.execute(
            select(SaverCheckpointBlob).where(SaverCheckpointBlob.thread_id == thread_id)
        )
    ).scalar_one()
    assert blob.blob == b"\x00\x01"
    assert blob.checkpoint_ns == ""
    server = (
        await session.execute(select(McpServer).where(McpServer.id == server_id))
    ).scalar_one()
    assert server.connection_status == "untested" and server.enabled is True
    bucket = (
        await session.execute(select(RateLimitBucket).where(RateLimitBucket.id == bucket_id))
    ).scalar_one()
    assert bucket.tokens == 2.5


async def test_timestamps_round_to_milliseconds(session: AsyncSession) -> None:
    """timestamp(3) rounds to milliseconds (123999us -> 124000us); it does not truncate."""
    moment = datetime(2026, 11, 20, 10, 0, 0, 123999, tzinfo=UTC)
    setting = DeploymentSetting(key=_unique("ts"), value="v", updated_at=moment)
    session.add(setting)
    await session.flush()
    session.expunge_all()
    loaded = (
        await session.execute(select(DeploymentSetting).where(DeploymentSetting.key == setting.key))
    ).scalar_one()
    assert loaded.updated_at == datetime(2026, 11, 20, 10, 0, 0, 124000, tzinfo=UTC)


async def test_updated_at_changes_on_update(session: AsyncSession) -> None:
    setting = DeploymentSetting(
        key=_unique("upd"), value="a", updated_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    session.add(setting)
    await session.flush()
    setting.value = "b"
    await session.flush()
    assert setting.updated_at > datetime.now(UTC) - timedelta(minutes=5)


async def test_none_in_a_required_json_column_fails_like_prisma(session: AsyncSession) -> None:
    session.add(Workflow(name=_unique("null-nodes"), nodes=None, edges=[]))
    with pytest.raises(IntegrityError):
        await session.flush()
