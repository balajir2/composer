"""Add durable execution attempts without backfilling legacy summaries.

Revision ID: 0004_execution_attempts
Revises: 0003_assignment_user_fks
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "0004_execution_attempts"
down_revision: str | None = "0003_assignment_user_fks"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

TABLE = "execution_attempts"
EXECUTION = "workflow_executions"


def _bound_locks() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")


def upgrade() -> None:
    _bound_locks()
    op.create_table(
        TABLE,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("execution_id", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            TIMESTAMP(precision=3, timezone=False),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("ended_at", TIMESTAMP(precision=3, timezone=False)),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("failure_code", sa.Text()),
        sa.Column("lease_owner", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", TIMESTAMP(precision=3, timezone=False), nullable=False),
        sa.Column("trigger_kind", sa.Text(), nullable=False),
        sa.Column("recovery_source_attempt_id", sa.Text()),
        sa.Column("approval_id", sa.Text()),
        sa.Column("actor_user_id", sa.Text()),
        sa.Column("reason_ref", sa.Text()),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["workflow_executions.id"],
            name="execution_attempts_execution_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recovery_source_attempt_id"],
            ["execution_attempts.id"],
            name="execution_attempts_recovery_source_attempt_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name="execution_attempts_approval_id_fkey",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("ordinal > 0", name="execution_attempts_ordinal_positive_check"),
        sa.CheckConstraint(
            "result IN ('running', 'paused', 'completed', 'failed', 'canceled')",
            name="execution_attempts_result_check",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('run', 'resume', 'recovery')",
            name="execution_attempts_trigger_kind_check",
        ),
        sa.CheckConstraint(
            "(result = 'running') = (ended_at IS NULL)",
            name="execution_attempts_running_ended_at_check",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="execution_attempts_ended_at_order_check",
        ),
    )
    op.create_index(
        "execution_attempts_execution_id_ordinal_key",
        TABLE,
        ["execution_id", "ordinal"],
        unique=True,
    )
    op.create_index(
        "execution_attempts_one_running_per_execution_key",
        TABLE,
        ["execution_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index("execution_attempts_started_at_id_idx", TABLE, ["started_at", "id"])
    op.create_index(
        "execution_attempts_result_started_at_id_idx", TABLE, ["result", "started_at", "id"]
    )
    op.create_index(
        "execution_attempts_recovery_source_attempt_id_idx", TABLE, ["recovery_source_attempt_id"]
    )
    op.create_index("execution_attempts_approval_id_idx", TABLE, ["approval_id"])
    op.add_column(EXECUTION, sa.Column("pending_attempt_approval_id", sa.Text()))
    op.create_foreign_key(
        "workflow_executions_pending_attempt_approval_id_fkey",
        EXECUTION,
        "approvals",
        ["pending_attempt_approval_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    _bound_locks()
    op.drop_constraint(
        "workflow_executions_pending_attempt_approval_id_fkey", EXECUTION, type_="foreignkey"
    )
    op.drop_column(EXECUTION, "pending_attempt_approval_id")
    op.drop_table(TABLE)
