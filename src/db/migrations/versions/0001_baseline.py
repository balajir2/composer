"""Baseline: the schema Prisma's 20 migrations created. No changes.

On an existing database, upgrading to this revision only records it in
``alembic_version``. Every later schema change is a new revision.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-20
"""

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Nothing to do: Prisma already created these tables."""


def downgrade() -> None:
    """Nothing to undo."""
