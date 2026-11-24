"""Foreign keys from workflow assignments to users.

``workflow_assignments.user_id`` and ``assigned_by_id`` were plain text with no
foreign key, so an assignment could point at a user that no longer exists (a grant
to a user deleted mid-request succeeded, and cleanup code had to delete assignments
by hand before deleting a user). This adds:

* ``user_id`` -> ``users.id`` ON DELETE CASCADE: deleting the assignee deletes
  their assignments.
* ``assigned_by_id`` -> ``users.id`` ON DELETE SET NULL: deleting the granter keeps
  the assignment, and the granter becomes unknown. The column therefore becomes
  nullable.
* an index on ``assigned_by_id``, so the SET NULL cascade does not scan the whole
  table every time a user is deleted. (``user_id`` is already indexed.)

Existing orphans are removed the same way the new constraints would have treated
them: assignments whose assignee no longer exists are deleted, and a granter that no
longer exists is set to NULL. Only the counts are logged, never user ids.

This runs on every deploy (``alembic upgrade head``). The table is small and the
whole migration is one transaction, so the table is locked up front in SHARE ROW
EXCLUSIVE mode (reads continue, writes wait) and the cleanup and the constraints are
atomic: no orphan can be inserted between the DELETE and the constraint check. The
constraints are added plainly rather than ``NOT VALID`` then ``VALIDATE``: inside one
transaction the stronger lock is held until commit either way, so the two-step form
would shorten nothing. Adding a foreign key also locks ``users`` (SHARE ROW
EXCLUSIVE), so both directions set a 5 s ``lock_timeout`` (and a 60 s
``statement_timeout``) with ``SET LOCAL``: a busy table fails the deploy fast and it
can be retried, instead of stalling the application.

Downgrade drops the constraints and index and restores NOT NULL on
``assigned_by_id``. That FAILS (and rolls back) once any assignment has a NULL
granter, which is expected: the old column cannot hold them, and the migration will
not invent a granter. Reassign or delete those rows first.

Revision ID: 0003_assignment_user_fks
Revises: 0002_idempotency_per_user
Create Date: 2026-11-24
"""

import logging

import sqlalchemy as sa
from alembic import op

revision: str = "0003_assignment_user_fks"
down_revision: str | None = "0002_idempotency_per_user"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "workflow_assignments"
USER_FK = "workflow_assignments_user_id_fkey"
BY_FK = "workflow_assignments_assigned_by_id_fkey"
BY_INDEX = "workflow_assignments_assigned_by_id_idx"


def _bound_locks() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")


def upgrade() -> None:
    _bound_locks()
    op.execute(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE")
    op.alter_column(TABLE, "assigned_by_id", existing_type=sa.Text(), nullable=True)
    bind = op.get_bind()
    removed = bind.execute(
        sa.text(
            f"DELETE FROM {TABLE} a WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = a.user_id)"
        )
    ).rowcount
    cleared = bind.execute(
        sa.text(
            f"UPDATE {TABLE} a SET assigned_by_id = NULL "
            "WHERE assigned_by_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = a.assigned_by_id)"
        )
    ).rowcount
    logger.info(
        "0003: removed %d orphaned assignments, cleared %d missing granters", removed, cleared
    )

    op.create_foreign_key(USER_FK, TABLE, "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(BY_FK, TABLE, "users", ["assigned_by_id"], ["id"], ondelete="SET NULL")
    op.create_index(BY_INDEX, TABLE, ["assigned_by_id"])


def downgrade() -> None:
    _bound_locks()
    op.drop_index(BY_INDEX, table_name=TABLE)
    op.drop_constraint(BY_FK, TABLE, type_="foreignkey")
    op.drop_constraint(USER_FK, TABLE, type_="foreignkey")
    # Fails if any granter is NULL; see the module docstring.
    op.alter_column(TABLE, "assigned_by_id", existing_type=sa.Text(), nullable=False)
