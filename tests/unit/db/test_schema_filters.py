from src.db.schema_filters import include_name


def test_alembic_bookkeeping_table_is_ignored() -> None:
    assert include_name("_prisma_migrations", "table", {}) is True  # dropped by 0006; not special
    assert include_name("alembic_version", "table", {}) is False


def test_application_tables_and_other_objects_are_included() -> None:
    assert include_name("workflows", "table", {}) is True
    assert include_name("workflows_user_id_idx", "index", {"table_name": "workflows"}) is True
