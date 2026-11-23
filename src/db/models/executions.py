"""Workflow runs, their approval decisions and streamed events."""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column
from src.db.models.enums import APPROVAL_DECISION, ApprovalDecision
from src.db.types import UTCDateTime


class WorkflowExecution(Base):
    """One run of a workflow: status, node results, input/output and delivery lease state."""

    __tablename__ = "workflow_executions"
    __table_args__ = (
        Index("workflow_executions_workflow_id_idx", "workflow_id"),
        Index("workflow_executions_user_id_idx", "user_id"),
        Index("workflow_executions_thread_id_key", "thread_id", unique=True),
        Index(
            "workflow_executions_workflow_id_user_id_idempotency_key_key",
            "workflow_id",
            "user_id",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[str] = id_column()
    workflow_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "workflows.id",
            name="workflow_executions_workflow_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )
    user_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    current_node_id: Mapped[str | None] = mapped_column(Text)
    node_results: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    variables: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    input: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = created_at_column()  # the row's creation time
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    thread_id: Mapped[str] = mapped_column(Text)
    original_owner_email: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    running_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failure_code: Mapped[str | None] = mapped_column(Text)
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class Approval(Base):
    """A user-approval decision (approved or rejected) recorded for a paused execution node."""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("approvals_execution_id_idx", "execution_id"),
        Index("approvals_approver_user_id_idx", "approver_user_id"),
    )

    id: Mapped[str] = id_column()
    execution_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "workflow_executions.id",
            name="approvals_execution_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )
    node_id: Mapped[str] = mapped_column(Text)
    approver_user_id: Mapped[str | None] = mapped_column(Text)
    approver_email: Mapped[str | None] = mapped_column(Text)
    via_email_link: Mapped[bool] = bool_column(False)
    decision: Mapped[ApprovalDecision] = mapped_column(APPROVAL_DECISION, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class ExecutionEvent(Base):
    """A sequence-numbered event emitted during an execution, replayed to streaming clients."""

    __tablename__ = "execution_events"
    __table_args__ = (
        Index("execution_events_execution_id_idx", "execution_id"),
        Index("execution_events_execution_id_seq_key", "execution_id", "seq", unique=True),
    )

    id: Mapped[str] = id_column()
    execution_id: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
