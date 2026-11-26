"""Base schema: the 20 Prisma migrations that built every pre-Alembic database, verbatim.

Until phase 6 of the SQLAlchemy migration, `prisma migrate deploy` created these tables and
this revision was empty. Prisma is gone now, so this revision creates them on an empty
database, with the exact SQL Prisma ran (oldest first). The tables are therefore identical to
every existing database's; tests/integration/db/test_baseline_schema.py compares a fresh build
with catalogs of a database Prisma built (tests/fixtures/schema/). Later changes, including
the two deliberate divergences from prisma/schema.prisma (0002, 0003), stay in their own
revisions.

Existing databases are never re-run: one with an alembic_version row is already past this
revision. A database that only Prisma built (no alembic_version, all 20 migrations recorded in
_prisma_migrations) is adopted: nothing runs and Alembic records this revision, as the old
empty baseline did. Anything else (a partial Prisma history, unknown Prisma migrations, or
tables without a Prisma history) is refused rather than guessed at.

"Empty" means: no table in the ``public`` schema other than ``alembic_version``. Other
schemas are ignored. Lock and statement timeouts are not set here (unlike 0002/0003): the
create path only ever runs against an empty schema, where no other session can hold a
conflicting lock, and env.py already serialises runs with an advisory lock.

Downgrading below this revision is refused: it would drop every table.

The SQL is frozen by tests/unit/db/test_baseline_frozen.py. Never edit it; write a new
revision.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-20 (DDL added 2026-11-26, SQLAlchemy migration phase 6)
"""

from typing import Literal

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

PRISMA_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "20260420081000_phase_1_execution_engine",
        """-- CreateTable
CREATE TABLE "workflows" (
    "id" TEXT NOT NULL,
    "user_id" TEXT,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "category" TEXT,
    "tags" TEXT[],
    "difficulty" TEXT,
    "estimated_time" TEXT,
    "nodes" JSONB NOT NULL,
    "edges" JSONB NOT NULL,
    "version" TEXT,
    "is_template" BOOLEAN NOT NULL DEFAULT false,
    "is_public" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "workflows_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "workflow_executions" (
    "id" TEXT NOT NULL,
    "workflow_id" TEXT NOT NULL,
    "user_id" TEXT,
    "status" TEXT NOT NULL,
    "current_node_id" TEXT,
    "node_results" JSONB NOT NULL,
    "variables" JSONB NOT NULL,
    "input" JSONB,
    "output" JSONB,
    "error" TEXT,
    "started_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completed_at" TIMESTAMP(3),
    "thread_id" TEXT NOT NULL,

    CONSTRAINT "workflow_executions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "langgraph_checkpoints" (
    "thread_id" TEXT NOT NULL,
    "checkpoint_ns" TEXT NOT NULL DEFAULT '',
    "checkpoint_id" TEXT NOT NULL,
    "parent_checkpoint_id" TEXT,
    "checkpoint" BYTEA NOT NULL,
    "metadata" BYTEA NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "langgraph_checkpoints_pkey" PRIMARY KEY ("thread_id","checkpoint_ns","checkpoint_id")
);

-- CreateTable
CREATE TABLE "langgraph_checkpoint_writes" (
    "thread_id" TEXT NOT NULL,
    "checkpoint_ns" TEXT NOT NULL DEFAULT '',
    "checkpoint_id" TEXT NOT NULL,
    "task_id" TEXT NOT NULL,
    "idx" INTEGER NOT NULL,
    "channel" TEXT NOT NULL,
    "value" BYTEA NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "langgraph_checkpoint_writes_pkey" PRIMARY KEY ("thread_id","checkpoint_ns","checkpoint_id","task_id","idx")
);

-- CreateIndex
CREATE INDEX "workflows_user_id_idx" ON "workflows"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "workflow_executions_thread_id_key" ON "workflow_executions"("thread_id");

-- CreateIndex
CREATE INDEX "workflow_executions_workflow_id_idx" ON "workflow_executions"("workflow_id");

-- CreateIndex
CREATE INDEX "workflow_executions_user_id_idx" ON "workflow_executions"("user_id");

-- CreateIndex
CREATE INDEX "langgraph_checkpoints_thread_id_idx" ON "langgraph_checkpoints"("thread_id");

-- CreateIndex
CREATE INDEX "langgraph_checkpoint_writes_thread_id_checkpoint_id_idx" ON "langgraph_checkpoint_writes"("thread_id", "checkpoint_id");

-- AddForeignKey
ALTER TABLE "workflow_executions" ADD CONSTRAINT "workflow_executions_workflow_id_fkey" FOREIGN KEY ("workflow_id") REFERENCES "workflows"("id") ON DELETE CASCADE ON UPDATE CASCADE;
""",
    ),
    (
        "20260420151148_phase_3a_mcp_server_table",
        """-- CreateTable
CREATE TABLE "mcp_servers" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "url" TEXT NOT NULL,
    "description" TEXT,
    "category" TEXT,
    "auth_type" TEXT NOT NULL,
    "encrypted_access_token" TEXT,
    "header_name" TEXT,
    "oauth_config" JSONB,
    "tools" JSONB,
    "connection_status" TEXT NOT NULL DEFAULT 'untested',
    "last_tested" TIMESTAMP(3),
    "last_error" TEXT,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "is_official" BOOLEAN NOT NULL DEFAULT false,
    "is_shared" BOOLEAN NOT NULL DEFAULT false,
    "headers" JSONB,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "mcp_servers_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "mcp_servers_user_id_idx" ON "mcp_servers"("user_id");

-- CreateIndex
CREATE INDEX "mcp_servers_is_shared_idx" ON "mcp_servers"("is_shared");
""",
    ),
    (
        "20260420165453_phase_3b_mcp_oauth",
        """-- CreateTable
CREATE TABLE "mcp_oauth_tokens" (
    "id" TEXT NOT NULL,
    "mcp_server_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "encrypted_access_token" TEXT NOT NULL,
    "encrypted_refresh_token" TEXT,
    "expires_at" TIMESTAMP(3),
    "scope" TEXT,
    "token_type" TEXT NOT NULL DEFAULT 'Bearer',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "mcp_oauth_tokens_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "mcp_oauth_states" (
    "id" TEXT NOT NULL,
    "mcp_server_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "code_verifier" TEXT NOT NULL,
    "redirect_uri" TEXT NOT NULL,
    "scope" TEXT,
    "expires_at" TIMESTAMP(3) NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "mcp_oauth_states_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "mcp_oauth_tokens_mcp_server_id_idx" ON "mcp_oauth_tokens"("mcp_server_id");

-- CreateIndex
CREATE INDEX "mcp_oauth_tokens_user_id_idx" ON "mcp_oauth_tokens"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "mcp_oauth_tokens_mcp_server_id_user_id_key" ON "mcp_oauth_tokens"("mcp_server_id", "user_id");

-- CreateIndex
CREATE UNIQUE INDEX "mcp_oauth_states_state_key" ON "mcp_oauth_states"("state");

-- CreateIndex
CREATE INDEX "mcp_oauth_states_state_idx" ON "mcp_oauth_states"("state");

-- CreateIndex
CREATE INDEX "mcp_oauth_states_expires_at_idx" ON "mcp_oauth_states"("expires_at");
""",
    ),
    (
        "20260421082218_phase_7a_user_table",
        """-- CreateEnum
CREATE TYPE "UserRole" AS ENUM ('admin', 'member');

-- CreateTable
CREATE TABLE "users" (
    "id" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "password_hash" TEXT NOT NULL,
    "display_name" TEXT,
    "role" "UserRole" NOT NULL DEFAULT 'member',
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE INDEX "users_email_idx" ON "users"("email");
""",
    ),
    (
        "20260421093812_phase_5a_approval",
        """-- CreateEnum
CREATE TYPE "ApprovalDecision" AS ENUM ('approved', 'rejected');

-- CreateTable
CREATE TABLE "approvals" (
    "id" TEXT NOT NULL,
    "execution_id" TEXT NOT NULL,
    "node_id" TEXT NOT NULL,
    "approver_user_id" TEXT NOT NULL,
    "decision" "ApprovalDecision" NOT NULL,
    "note" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "approvals_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "approvals_execution_id_idx" ON "approvals"("execution_id");

-- CreateIndex
CREATE INDEX "approvals_approver_user_id_idx" ON "approvals"("approver_user_id");

-- AddForeignKey
ALTER TABLE "approvals" ADD CONSTRAINT "approvals_execution_id_fkey" FOREIGN KEY ("execution_id") REFERENCES "workflow_executions"("id") ON DELETE CASCADE ON UPDATE CASCADE;
""",
    ),
    (
        "20260422105743_phase9b_original_owner_email",
        """-- AlterTable
ALTER TABLE "mcp_servers" ADD COLUMN     "original_owner_email" TEXT;

-- AlterTable
ALTER TABLE "workflow_executions" ADD COLUMN     "original_owner_email" TEXT;

-- AlterTable
ALTER TABLE "workflows" ADD COLUMN     "original_owner_email" TEXT;
""",
    ),
    (
        "20260422125251_phase9b_mcp_server_user_id_nullable",
        """-- AlterTable
ALTER TABLE "mcp_servers" ALTER COLUMN "user_id" DROP NOT NULL;
""",
    ),
    (
        "20260422132306_phase9e_llm_api_keys",
        """-- CreateTable
CREATE TABLE "llm_api_keys" (
    "id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "encrypted_key" TEXT NOT NULL,
    "key_prefix" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "llm_api_keys_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "llm_api_keys_provider_key" ON "llm_api_keys"("provider");
""",
    ),
    (
        "20260423083245_phase10d_deployment_settings",
        """-- CreateTable
CREATE TABLE "deployment_settings" (
    "key" TEXT NOT NULL,
    "value" TEXT NOT NULL,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deployment_settings_pkey" PRIMARY KEY ("key")
);
""",
    ),
    (
        "20260423110122_phase10a_production_workflows_and_api_keys",
        """-- AlterTable
ALTER TABLE "workflows" ADD COLUMN "is_production" BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE "workflows" ADD COLUMN "external_slug" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "workflows_external_slug_key" ON "workflows"("external_slug");

-- CreateIndex
CREATE INDEX "workflows_external_slug_idx" ON "workflows"("external_slug");

-- AlterTable
ALTER TABLE "users" ALTER COLUMN "password_hash" DROP NOT NULL;

-- CreateTable
CREATE TABLE "api_keys" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "label" TEXT NOT NULL,
    "key_hash" TEXT NOT NULL,
    "key_prefix" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_used_at" TIMESTAMP(3),
    "expires_at" TIMESTAMP(3),
    "revoked_at" TIMESTAMP(3),

    CONSTRAINT "api_keys_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "api_keys_key_hash_key" ON "api_keys"("key_hash");

-- CreateIndex
CREATE INDEX "api_keys_user_id_idx" ON "api_keys"("user_id");

-- CreateIndex
CREATE INDEX "api_keys_key_prefix_idx" ON "api_keys"("key_prefix");

-- AddForeignKey
ALTER TABLE "api_keys" ADD CONSTRAINT "api_keys_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
""",
    ),
    (
        "20260423125304_phase10_llm_models",
        """-- CreateTable
CREATE TABLE "llm_models" (
    "id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "model_id" TEXT NOT NULL,
    "label" TEXT,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "llm_models_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "llm_models_provider_enabled_idx" ON "llm_models"("provider", "enabled");

-- CreateIndex
CREATE UNIQUE INDEX "llm_models_provider_model_id_key" ON "llm_models"("provider", "model_id");
""",
    ),
    (
        "20260423132556_phase10_user_isactive",
        """-- AlterTable
ALTER TABLE "users" ADD COLUMN     "is_active" BOOLEAN NOT NULL DEFAULT true;
""",
    ),
    (
        "20260427043224_phase10_llmmodel_verification",
        """-- AlterTable
ALTER TABLE "llm_models" ADD COLUMN     "verification_message" TEXT,
ADD COLUMN     "verification_status" TEXT,
ADD COLUMN     "verified_at" TIMESTAMP(3);
""",
    ),
    (
        "20260709121238_add_must_change_password",
        """-- AlterTable
ALTER TABLE "users" ADD COLUMN     "must_change_password" BOOLEAN NOT NULL DEFAULT false;
""",
    ),
    (
        "20260709162221_add_workflow_assignment",
        """-- CreateTable
CREATE TABLE "workflow_assignments" (
    "id" TEXT NOT NULL,
    "workflow_id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "assigned_by_id" TEXT NOT NULL,
    "assigned_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "workflow_assignments_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "workflow_assignments_workflow_id_idx" ON "workflow_assignments"("workflow_id");

-- CreateIndex
CREATE INDEX "workflow_assignments_user_id_idx" ON "workflow_assignments"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "workflow_assignments_workflow_id_user_id_key" ON "workflow_assignments"("workflow_id", "user_id");

-- AddForeignKey
ALTER TABLE "workflow_assignments" ADD CONSTRAINT "workflow_assignments_workflow_id_fkey" FOREIGN KEY ("workflow_id") REFERENCES "workflows"("id") ON DELETE CASCADE ON UPDATE CASCADE;
""",
    ),
    (
        "20260711090800_approval_email_fields",
        """-- AlterTable
ALTER TABLE "approvals" ADD COLUMN     "approver_email" TEXT,
ADD COLUMN     "via_email_link" BOOLEAN NOT NULL DEFAULT false,
ALTER COLUMN "approver_user_id" DROP NOT NULL;
""",
    ),
    (
        "20260712150606_add_execution_idempotency_key",
        """-- AlterTable
ALTER TABLE "workflow_executions" ADD COLUMN     "idempotency_key" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "workflow_executions_workflow_id_idempotency_key_key" ON "workflow_executions"("workflow_id", "idempotency_key");
""",
    ),
    (
        "20260713161628_durable_execution",
        """-- AlterTable
ALTER TABLE "workflow_executions" ADD COLUMN     "delivery_attempts" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN     "lease_expires_at" TIMESTAMP(3),
ADD COLUMN     "lease_owner" TEXT;

-- CreateTable
CREATE TABLE "execution_events" (
    "id" TEXT NOT NULL,
    "execution_id" TEXT NOT NULL,
    "seq" INTEGER NOT NULL,
    "type" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "execution_events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "rate_limit_buckets" (
    "id" TEXT NOT NULL,
    "route_key" TEXT NOT NULL,
    "client_key" TEXT NOT NULL,
    "tokens" DOUBLE PRECISION NOT NULL,
    "last_refill" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "rate_limit_buckets_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "execution_events_execution_id_idx" ON "execution_events"("execution_id");

-- CreateIndex
CREATE UNIQUE INDEX "execution_events_execution_id_seq_key" ON "execution_events"("execution_id", "seq");

-- CreateIndex
CREATE UNIQUE INDEX "rate_limit_buckets_route_key_client_key_key" ON "rate_limit_buckets"("route_key", "client_key");
""",
    ),
    (
        "20260716141033_add_cloud_storage_connection",
        """-- CreateTable
CREATE TABLE "cloud_storage_connections" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "account_email" TEXT NOT NULL,
    "encrypted_access_token" TEXT NOT NULL,
    "encrypted_refresh_token" TEXT,
    "expires_at" TIMESTAMP(3),
    "scope" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "cloud_storage_connections_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "cloud_storage_connections_user_id_idx" ON "cloud_storage_connections"("user_id");

-- CreateIndex
CREATE UNIQUE INDEX "cloud_storage_connections_user_id_provider_account_email_key" ON "cloud_storage_connections"("user_id", "provider", "account_email");
""",
    ),
    (
        "20261117000000_execution_attempt_timing",
        """ALTER TABLE "workflow_executions"
ADD COLUMN "running_since" TIMESTAMP(3),
ADD COLUMN "failure_code" TEXT;
""",
    ),
)

PRISMA_MIGRATION_NAMES: tuple[str, ...] = tuple(name for name, _ in PRISMA_MIGRATIONS)


def _statements(sql: str) -> list[str]:
    """Split a migration into statements. The Prisma SQL has no functions, DO blocks or
    quoted semicolons, so splitting on ';' is exact. Comment-only chunks are skipped."""
    out: list[str] = []
    for chunk in sql.split(";"):
        code = [ln for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if code:
            out.append(chunk.strip())
    return out


def _base_state(bind: Connection) -> Literal["create", "adopt"]:
    tables = {
        r[0]
        for r in bind.exec_driver_sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    } - {"alembic_version"}
    if not tables:
        return "create"
    if "_prisma_migrations" not in tables:
        raise RuntimeError(
            "0001_baseline: the public schema has tables but no Prisma history "
            "(_prisma_migrations); refusing to adopt a schema of unknown shape. Restore the "
            "database or build it from an empty one."
        )
    applied = {
        r[0]
        for r in bind.exec_driver_sql(
            "SELECT migration_name FROM _prisma_migrations "
            "WHERE finished_at IS NOT NULL AND rolled_back_at IS NULL"
        )
    }
    missing = [n for n in PRISMA_MIGRATION_NAMES if n not in applied]
    unknown = sorted(applied - set(PRISMA_MIGRATION_NAMES))
    if missing or unknown:
        raise RuntimeError(
            "0001_baseline: this database was built by Prisma but is not at its final "
            f"migration; missing Prisma migrations: {', '.join(missing) or 'none'}; unknown: "
            f"{', '.join(unknown) or 'none'}. Finish it from a pre-phase-6 checkout "
            "(git checkout 58d2829 && uv run prisma migrate deploy) or restore it."
        )
    return "adopt"


def upgrade() -> None:
    bind = op.get_bind()
    if _base_state(bind) == "adopt":
        return
    for _, sql in PRISMA_MIGRATIONS:
        for statement in _statements(sql):
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise RuntimeError(
        "0001 is the base schema; downgrading below it would drop every table. "
        "Restore from a backup instead."
    )
