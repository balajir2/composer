from src.db.schema_filters import include_name


def test_prisma_and_alembic_bookkeeping_tables_are_ignored() -> None:
    assert include_name("_prisma_migrations", "table", {}) is False
    assert include_name("alembic_version", "table", {}) is False


def test_application_tables_and_other_objects_are_included() -> None:
    assert include_name("workflows", "table", {}) is True
    assert include_name("workflows_user_id_idx", "index", {"table_name": "workflows"}) is True
