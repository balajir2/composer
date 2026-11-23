"""Scope execution idempotency keys per user: unique on (workflow, user, key).

The key used to be unique per workflow, so anyone who could start runs of a
shared or public workflow could claim another user's key (the real caller then
got a 409) or probe whether a key had been used. Scoping it to the caller removes
both. The old and new uniqueness are Postgres unique indexes (Prisma created the
old one as ``CREATE UNIQUE INDEX``), so they are swapped with index operations.

Upgrade creates the new index first, then drops the old one, in one transaction,
so uniqueness is never missing. The new index is strictly looser than the old, so
existing rows always satisfy it and the upgrade cannot fail on data.

Downgrade restores the old index and FAILS if two users now share a key on one
workflow. That is expected: those rows cannot coexist under the old rule, and
the migration will not delete anyone's executions to make it pass.

Revision ID: 0002_idempotency_per_user
Revises: 0001_baseline
Create Date: 2026-11-23
"""

from alembic import op

revision: str = "0002_idempotency_per_user"
down_revision: str | None = "0001_baseline"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

TABLE = "workflow_executions"
OLD_INDEX = "workflow_executions_workflow_id_idempotency_key_key"
NEW_INDEX = "workflow_executions_workflow_id_user_id_idempotency_key_key"


def upgrade() -> None:
    op.create_index(NEW_INDEX, TABLE, ["workflow_id", "user_id", "idempotency_key"], unique=True)
    op.drop_index(OLD_INDEX, table_name=TABLE)


def downgrade() -> None:
    op.create_index(OLD_INDEX, TABLE, ["workflow_id", "idempotency_key"], unique=True)
    op.drop_index(NEW_INDEX, table_name=TABLE)
