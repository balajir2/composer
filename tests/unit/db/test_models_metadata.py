from sqlalchemy import CheckConstraint, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB

from src.db.models import AttemptResult, AttemptTriggerKind, Base

EXPECTED_TABLES = {
    "api_keys",
    "approvals",
    # Added by alembic 0004 (the official saver's tables; not in Prisma's frozen schema).
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
    "cloud_storage_connections",
    "deployment_settings",
    "execution_events",
    "execution_attempts",
    "llm_api_keys",
    "llm_models",
    "mcp_oauth_states",
    "mcp_oauth_tokens",
    "mcp_servers",
    "rate_limit_buckets",
    "users",
    "workflow_assignments",
    "workflow_executions",
    "workflows",
}


def test_every_table_is_mapped_exactly_once() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_foreign_keys_cascade_like_prisma_created_them() -> None:
    fks = {
        (table.name, fk.parent.name, fk.column.table.name, fk.ondelete, fk.onupdate)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
    }
    assert fks == {
        ("api_keys", "user_id", "users", "CASCADE", "CASCADE"),
        ("approvals", "execution_id", "workflow_executions", "CASCADE", "CASCADE"),
        ("execution_attempts", "execution_id", "workflow_executions", "CASCADE", "CASCADE"),
        ("execution_attempts", "approval_id", "approvals", "SET NULL", None),
        (
            "execution_attempts",
            "recovery_source_attempt_id",
            "execution_attempts",
            "SET NULL",
            None,
        ),
        ("workflow_assignments", "workflow_id", "workflows", "CASCADE", "CASCADE"),
        # Added by alembic 0003 (Prisma's frozen schema has no foreign keys to users).
        ("workflow_assignments", "user_id", "users", "CASCADE", None),
        ("workflow_assignments", "assigned_by_id", "users", "SET NULL", None),
        ("workflow_executions", "workflow_id", "workflows", "CASCADE", "CASCADE"),
        ("workflow_executions", "pending_attempt_approval_id", "approvals", "SET NULL", None),
    }


def test_unique_keys_are_unique_indexes_named_like_prisma() -> None:
    unique_indexes = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.unique
    }
    assert unique_indexes == {
        "api_keys_key_hash_key",
        "cloud_storage_connections_user_id_provider_account_email_key",
        "execution_events_execution_id_seq_key",
        "execution_attempts_execution_id_ordinal_key",
        "execution_attempts_one_running_per_execution_key",
        "llm_api_keys_provider_key",
        "llm_models_provider_model_id_key",
        "mcp_oauth_states_state_key",
        "mcp_oauth_tokens_mcp_server_id_user_id_key",
        "rate_limit_buckets_route_key_client_key_key",
        "users_email_key",
        "workflow_assignments_workflow_id_user_id_key",
        "workflow_executions_thread_id_key",
        "workflow_executions_workflow_id_user_id_idempotency_key_key",
        "workflows_external_slug_key",
    }


def test_execution_attempt_indexes_and_pending_approval() -> None:
    expected_attempt_indexes = {
        "execution_attempts_execution_id_ordinal_key",
        "execution_attempts_one_running_per_execution_key",
        "execution_attempts_started_at_id_idx",
        "execution_attempts_result_started_at_id_idx",
        "execution_attempts_recovery_source_attempt_id_idx",
        "execution_attempts_approval_id_idx",
    }
    table = Base.metadata.tables["execution_attempts"]
    assert expected_attempt_indexes <= {index.name for index in table.indexes}
    assert "pending_attempt_approval_id" in Base.metadata.tables["workflow_executions"].c


def test_attempt_values_use_text_with_named_database_checks() -> None:
    table = Base.metadata.tables["execution_attempts"]
    assert isinstance(table.c.result.type, Text)
    assert isinstance(table.c.trigger_kind.type, Text)
    assert {item.value for item in AttemptResult} == {
        "running",
        "paused",
        "completed",
        "failed",
        "canceled",
    }
    assert {item.value for item in AttemptTriggerKind} == {"run", "resume", "recovery"}
    assert {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        "execution_attempts_ordinal_positive_check",
        "execution_attempts_result_check",
        "execution_attempts_trigger_kind_check",
        "execution_attempts_running_ended_at_check",
        "execution_attempts_ended_at_order_check",
    }


def test_enum_types_are_reused_not_created() -> None:
    role = Base.metadata.tables["users"].c.role.type
    decision = Base.metadata.tables["approvals"].c.decision.type
    assert isinstance(role, ENUM)
    assert isinstance(decision, ENUM)
    assert role.name == "UserRole"
    assert decision.name == "ApprovalDecision"
    assert role.create_type is False
    assert decision.create_type is False


def test_enum_labels_match_the_database() -> None:
    role = Base.metadata.tables["users"].c.role.type
    decision = Base.metadata.tables["approvals"].c.decision.type
    assert isinstance(role, ENUM)
    assert isinstance(decision, ENUM)
    assert list(role.enums) == ["admin", "member"]
    assert list(decision.enums) == ["approved", "rejected"]


def test_checkpoint_writes_primary_key_order() -> None:
    table = Base.metadata.tables["checkpoint_writes"]
    assert [c.name for c in table.primary_key.columns] == [
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "task_id",
        "idx",
    ]
    assert table.c.idx.autoincrement is False


def test_timestamp_defaults_are_owned_by_the_right_side() -> None:
    created = {"created_at", "started_at", "assigned_at"}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name in created:
                assert column.server_default is not None, f"{table.name}.{column.name}"
            elif column.name == "updated_at":
                assert column.server_default is None, f"{table.name}.{column.name}"


def test_optional_json_columns_store_none_as_sql_null() -> None:
    nullable_jsonb = {
        f"{table.name}.{column.name}": column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSONB) and column.nullable
    }
    assert set(nullable_jsonb) == {
        "workflow_executions.input",
        "workflow_executions.output",
        "mcp_servers.oauth_config",
        "mcp_servers.tools",
        "mcp_servers.headers",
    }
    for name, column in nullable_jsonb.items():
        assert isinstance(column.type, JSONB)
        assert column.type.none_as_null is True, name


def test_every_jsonb_column_rejects_none_instead_of_storing_json_null() -> None:
    jsonb_columns = [
        (f"{table.name}.{column.name}", column)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, JSONB)
    ]
    assert jsonb_columns
    for name, column in jsonb_columns:
        assert isinstance(column.type, JSONB)
        assert column.type.none_as_null is True, name
