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
        "workflow_executions_workflow_id_idempotency_key_key",
        "workflows_external_slug_key",
    }


def test_enum_types_are_reused_not_created() -> None:
    role = Base.metadata.tables["users"].c.role.type
    decision = Base.metadata.tables["approvals"].c.decision.type
    assert getattr(role, "name", None) == "UserRole"
    assert getattr(decision, "name", None) == "ApprovalDecision"
    assert getattr(role, "create_type", True) is False
    assert getattr(decision, "create_type", True) is False
