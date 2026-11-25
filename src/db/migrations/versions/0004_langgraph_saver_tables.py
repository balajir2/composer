"""Tables for LangGraph's official Postgres checkpointer (langgraph-checkpoint-postgres 3.2.1).

The DDL is the library's own MIGRATIONS list (langgraph/checkpoint/postgres/base.py) applied in
order and collapsed to its final state: blob nullable, task_path present, three thread_id
indexes. The ten rows in checkpoint_migrations tell the library every one of its migrations has
run, so AsyncPostgresSaver.setup() is never needed (and is never called by the app or deploys:
schema changes go through Alembic only). tests/integration/db/test_saver_schema_matches_setup.py
proves this revision and setup() produce identical tables.

Plain CREATE TABLE, not IF NOT EXISTS: if a table of the same name already exists (someone ran
setup() by hand), the deploy fails instead of silently adopting a table of unknown shape. The
indexes are created without CONCURRENTLY: the tables are new and empty, and env.py runs every
pending revision in one transaction, where CONCURRENTLY is not allowed.

All four tables are new, so the upgrade locks nothing that the running application uses. The
5 s lock_timeout and 60 s statement_timeout are still set (with SET LOCAL) like 0002 and 0003, so
a surprise cannot stall a deploy. The downgrade drops tables and does contend with writers.

The old tables (langgraph_checkpoints, langgraph_checkpoint_writes) are untouched; phase 7 drops
them. Like 0002 and 0003 this schema is not in prisma/schema.prisma (frozen); phase 6's squashed
base must include these tables and the ten checkpoint_migrations rows.

Downgrade refuses while any checkpoint exists in the new tables: those are runs that paused
after the cutover, and dropping them would make them impossible to resume. Copy them to the old
tables first (`composer checkpoints copy --to legacy --all-waiting`) and delete the rows.

Revision ID: 0004_langgraph_saver_tables
Revises: 0003_assignment_user_fks
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004_langgraph_saver_tables"
down_revision: str | None = "0003_assignment_user_fks"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

SAVER_MIGRATIONS_APPLIED = 10  # len(BasePostgresSaver.MIGRATIONS) in 3.2.1; pinned by a unit test


def _bound_locks() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")


def upgrade() -> None:
    _bound_locks()
    op.execute("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)")
    op.execute(
        """CREATE TABLE checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
)"""
    )
    op.execute(
        """CREATE TABLE checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
)"""
    )
    op.execute(
        """CREATE TABLE checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
)"""
    )
    op.execute("CREATE INDEX checkpoints_thread_id_idx ON checkpoints(thread_id)")
    op.execute("CREATE INDEX checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)")
    op.execute("CREATE INDEX checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)")
    op.execute(
        "INSERT INTO checkpoint_migrations (v) "
        f"SELECT generate_series(0, {SAVER_MIGRATIONS_APPLIED - 1})"
    )


def downgrade() -> None:
    _bound_locks()
    remaining = op.get_bind().scalar(sa.text("SELECT count(*) FROM checkpoints"))
    if remaining:
        raise RuntimeError(
            f"{remaining} checkpoints exist in the new tables; copy paused runs to the old "
            "tables (composer checkpoints copy --to legacy --all-waiting) and delete them first"
        )
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
        op.execute(f"DROP TABLE {table}")
