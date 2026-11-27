"""Durable records of individual processing attempts for a workflow execution."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, created_at_column, id_column
from src.db.types import UTCDateTime


class AttemptResult(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class AttemptTriggerKind(StrEnum):
    RUN = "run"
    RESUME = "resume"
    RECOVERY = "recovery"


class ExecutionAttempt(Base):
    """One leased processing interval, including its provenance and outcome."""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        CheckConstraint("ordinal > 0", name="execution_attempts_ordinal_positive_check"),
        CheckConstraint(
            "result IN ('running', 'paused', 'completed', 'failed', 'canceled')",
            name="execution_attempts_result_check",
        ),
        CheckConstraint(
            "trigger_kind IN ('run', 'resume', 'recovery')",
            name="execution_attempts_trigger_kind_check",
        ),
        CheckConstraint(
            "(result = 'running') = (ended_at IS NULL)",
            name="execution_attempts_running_ended_at_check",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="execution_attempts_ended_at_order_check",
        ),
        Index(
            "execution_attempts_execution_id_ordinal_key", "execution_id", "ordinal", unique=True
        ),
        Index(
            "execution_attempts_one_running_per_execution_key",
            "execution_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("execution_attempts_started_at_id_idx", "started_at", "id"),
        Index("execution_attempts_result_started_at_id_idx", "result", "started_at", "id"),
        Index("execution_attempts_recovery_source_attempt_id_idx", "recovery_source_attempt_id"),
        Index("execution_attempts_approval_id_idx", "approval_id"),
    )

    id: Mapped[str] = id_column()
    execution_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "workflow_executions.id",
            name="execution_attempts_execution_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = created_at_column()
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result: Mapped[AttemptResult] = mapped_column(Text, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str] = mapped_column(Text, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trigger_kind: Mapped[AttemptTriggerKind] = mapped_column(Text, nullable=False)
    recovery_source_attempt_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "execution_attempts.id",
            name="execution_attempts_recovery_source_attempt_id_fkey",
            ondelete="SET NULL",
        ),
    )
    approval_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("approvals.id", name="execution_attempts_approval_id_fkey", ondelete="SET NULL"),
    )
    actor_user_id: Mapped[str | None] = mapped_column(Text)
    reason_ref: Mapped[str | None] = mapped_column(Text)
