from sqlalchemy.dialects.postgresql import ENUM, JSONB

from src.db.models import Base

EXPECTED_TABLES = {
    "api_keys",
    "approvals",
    "cloud_storage_connections",
    "deployment_settings",
    "execution_events",
    "langgraph_checkpoint_writes",
    "langgraph_checkpoints",
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


def test_every_prisma_table_is_mapped_exactly_once() -> None:
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
        ("workflow_assignments", "workflow_id", "workflows", "CASCADE", "CASCADE"),
        # Added by alembic 0003 (Prisma's frozen schema has no foreign keys to users).
        ("workflow_assignments", "user_id", "users", "CASCADE", None),
        ("workflow_assignments", "assigned_by_id", "users", "SET NULL", None),
        ("workflow_executions", "workflow_id", "workflows", "CASCADE", "CASCADE"),
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
    table = Base.metadata.tables["langgraph_checkpoint_writes"]
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
