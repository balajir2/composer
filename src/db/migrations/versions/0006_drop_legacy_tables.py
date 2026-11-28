"""Drop what only the Prisma era used: the old LangGraph checkpoint tables and Prisma's ledger.

SQLAlchemy migration phase 7 (ADR-0035, ADR-0036, ADR-0037). Since phase 5 (2026-11-25) every
checkpoint is written by the official saver (0004's tables). langgraph_checkpoints and
langgraph_checkpoint_writes were kept only so a paused run could be copied between the savers and
a rollback was possible; _prisma_migrations was kept only for pre-phase-6 deploy workflows, which
cannot run against a database at 0005 anyway.

Upgrade, in env.py's single transaction: bound lock waits (5 s) and statements (60 s); lock the
two old tables ACCESS EXCLUSIVE (writes first, the order delete_threads uses); refuse while a
queued, running or waiting_approval execution has a (thread, namespace) whose newest old-table
checkpoint id is missing from, or newer than, the newest in `checkpoints` (the phase 5 deploy
guard's query, frozen here; ids are time-ordered UUIDv6 strings compared COLLATE "C"); then drop
the two tables, and _prisma_migrations if it exists (databases built by Alembic alone never had
it). The refusal names execution ids and statuses only, and the copy tool, which lives at commit
6ce5495 (the last release that has it): the database is untouched, so that checkout's
`composer checkpoints copy --to postgres` still works.

Downgrade recreates the two checkpoint tables EMPTY with 0001's exact DDL (checked by
tests/unit/db/test_revision_0006.py), so an image from before phase 7 (which reads them on every
resume and delete) runs correctly after a rollback. Their rows are NOT restored: restore from a
backup if the history matters. _prisma_migrations is deliberately not recreated: only 0001 reads
it, and only on a database without alembic_version; an empty ledger would make a pre-phase-6
`prisma migrate deploy` re-apply its migrations; invented rows would be fabricated history.

Revision ID: 0006_drop_legacy_tables
Revises: 0005_execution_attempts
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006_drop_legacy_tables"
down_revision: str | None = "0005_execution_attempts"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

COPY_TOOL_REF = "6ce5495"  # last commit with `composer checkpoints copy`
ACTIVE_STATUSES = ("queued", "running", "waiting_approval")
SHOWN = 20

LEGACY_ONLY_ACTIVE_RUNS = """
SELECT e.id, e.status
FROM (
    SELECT thread_id, checkpoint_ns, max(checkpoint_id COLLATE "C") AS cid
    FROM langgraph_checkpoints GROUP BY thread_id, checkpoint_ns
) old
JOIN workflow_executions e ON e.thread_id = old.thread_id
LEFT JOIN (
    SELECT thread_id, checkpoint_ns, max(checkpoint_id COLLATE "C") AS cid
    FROM checkpoints GROUP BY thread_id, checkpoint_ns
) nw ON nw.thread_id = old.thread_id AND nw.checkpoint_ns = old.checkpoint_ns
WHERE e.status IN ('queued', 'running', 'waiting_approval')
  AND (nw.cid IS NULL OR nw.cid < old.cid)
GROUP BY e.id, e.status
ORDER BY e.id
"""

ACTIVE_COUNTS = (
    "SELECT status, count(*) FROM workflow_executions "
    "WHERE status IN ('queued', 'running', 'waiting_approval') GROUP BY status"
)

# Verbatim from 0001_baseline (Prisma migration 20260420081000_phase_1_execution_engine).
CREATE_OLD_TABLES = (
    """CREATE TABLE "langgraph_checkpoints" (
    "thread_id" TEXT NOT NULL,
    "checkpoint_ns" TEXT NOT NULL DEFAULT '',
    "checkpoint_id" TEXT NOT NULL,
    "parent_checkpoint_id" TEXT,
    "checkpoint" BYTEA NOT NULL,
    "metadata" BYTEA NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "langgraph_checkpoints_pkey" PRIMARY KEY ("thread_id","checkpoint_ns","checkpoint_id")
)""",
    """CREATE TABLE "langgraph_checkpoint_writes" (
    "thread_id" TEXT NOT NULL,
    "checkpoint_ns" TEXT NOT NULL DEFAULT '',
    "checkpoint_id" TEXT NOT NULL,
    "task_id" TEXT NOT NULL,
    "idx" INTEGER NOT NULL,
    "channel" TEXT NOT NULL,
    "value" BYTEA NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "langgraph_checkpoint_writes_pkey" PRIMARY KEY ("thread_id","checkpoint_ns","checkpoint_id","task_id","idx")
)""",
    """CREATE INDEX "langgraph_checkpoints_thread_id_idx" ON "langgraph_checkpoints"("thread_id")""",
    """CREATE INDEX "langgraph_checkpoint_writes_thread_id_checkpoint_id_idx" ON "langgraph_checkpoint_writes"("thread_id", "checkpoint_id")""",
)


def _bound_locks() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")


def upgrade() -> None:
    _bound_locks()
    bind = op.get_bind()
    op.execute(
        "LOCK TABLE langgraph_checkpoint_writes, langgraph_checkpoints IN ACCESS EXCLUSIVE MODE"
    )
    counts = dict.fromkeys(ACTIVE_STATUSES, 0)
    for status, n in bind.execute(sa.text(ACTIVE_COUNTS)):
        counts[str(status)] = int(n)
    print("0006: active runs " + " ".join(f"{s}={counts[s]}" for s in ACTIVE_STATUSES), flush=True)
    blocked = [(str(r[0]), str(r[1])) for r in bind.execute(sa.text(LEGACY_ONLY_ACTIVE_RUNS))]
    if blocked:
        shown = ", ".join(f"{eid} ({status})" for eid, status in blocked[:SHOWN])
        more = f" and {len(blocked) - SHOWN} more" if len(blocked) > SHOWN else ""
        raise RuntimeError(
            f"0006_drop_legacy_tables: {len(blocked)} active run(s) have their newest checkpoint "
            f"only in the old tables: {shown}{more}. Nothing was dropped. Copy them forward with "
            f"the last release that has the copy tool (git checkout {COPY_TOOL_REF} && uv run "
            "composer checkpoints copy --to postgres --execution <id>, direct DATABASE_URL), or "
            "cancel them; then re-run the deploy."
        )
    op.execute("DROP TABLE langgraph_checkpoint_writes")
    op.execute("DROP TABLE langgraph_checkpoints")
    op.execute("DROP TABLE IF EXISTS _prisma_migrations")


def downgrade() -> None:
    _bound_locks()
    for statement in CREATE_OLD_TABLES:
        op.execute(statement)
