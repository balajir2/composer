"""Add durable execution attempts without backfilling legacy summaries.

The new foreign key on workflow_executions.pending_attempt_approval_id is added NOT VALID and
validated in a second statement, so the full-table validation scan does not run under the
ACCESS EXCLUSIVE lock that ADD CONSTRAINT takes (VALIDATE CONSTRAINT only needs SHARE UPDATE
EXCLUSIVE). A partial index on the column keeps ON DELETE SET NULL from scanning the table
when an approval is deleted.

Downgrade refuses while attempt history exists or any execution carries a pending attempt
approval: dropping them would silently destroy the only record of past attempts. Export the
rows first, then `TRUNCATE execution_attempts` and
`UPDATE workflow_executions SET pending_attempt_approval_id = NULL`, then downgrade.

Revision ID: 0005_execution_attempts
Revises: 0004_langgraph_saver_tables
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "0005_execution_attempts"
down_revision: str | None = "0004_langgraph_saver_tables"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

TABLE = "execution_attempts"
EXECUTION = "workflow_executions"
PENDING_FK = "workflow_executions_pending_attempt_approval_id_fkey"
PENDING_IDX = "workflow_executions_pending_attempt_approval_id_idx"


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
    op.execute(
        f"ALTER TABLE {EXECUTION} ADD CONSTRAINT {PENDING_FK} "
        "FOREIGN KEY (pending_attempt_approval_id) REFERENCES approvals (id) "
        "ON DELETE SET NULL NOT VALID"
    )
    op.execute(f"ALTER TABLE {EXECUTION} VALIDATE CONSTRAINT {PENDING_FK}")
    op.create_index(
        PENDING_IDX,
        EXECUTION,
        ["pending_attempt_approval_id"],
        postgresql_where=sa.text("pending_attempt_approval_id IS NOT NULL"),
    )


def downgrade() -> None:
    _bound_locks()
    bind = op.get_bind()
    attempts = bind.scalar(sa.text(f"SELECT count(*) FROM {TABLE}")) or 0
    pending = (
        bind.scalar(
            sa.text(
                f"SELECT count(*) FROM {EXECUTION} WHERE pending_attempt_approval_id IS NOT NULL"
            )
        )
        or 0
    )
    if attempts or pending:
        raise RuntimeError(
            f"refusing to downgrade: {attempts} execution_attempts rows and {pending} "
            "workflow_executions rows with pending_attempt_approval_id set would be destroyed. "
            "To proceed deliberately, export execution_attempts first, then run "
            "`TRUNCATE execution_attempts` and "
            "`UPDATE workflow_executions SET pending_attempt_approval_id = NULL`, "
            "and downgrade again."
        )
    op.drop_index(PENDING_IDX, table_name=EXECUTION)
    op.drop_constraint(PENDING_FK, EXECUTION, type_="foreignkey")
    op.drop_column(EXECUTION, "pending_attempt_approval_id")
    op.drop_table(TABLE)
