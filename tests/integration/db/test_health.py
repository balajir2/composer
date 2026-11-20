import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.db.health import alembic_head_revision, check_database

pytestmark = pytest.mark.integration


async def test_reachable_database_on_head_revision_is_current(engine: AsyncEngine) -> None:
    health = await check_database(
        engine, timeout_seconds=5, expected_revision=alembic_head_revision()
    )
    assert health.reachable and health.schema_current is True and health.error is None


async def test_wrong_expected_revision_is_reported_not_raised(engine: AsyncEngine) -> None:
    health = await check_database(engine, timeout_seconds=5, expected_revision="not-a-revision")
    assert health.reachable and health.schema_current is False


async def test_unreachable_database_reports_class_name_only() -> None:
    bad = create_async_engine(
        "postgresql+psycopg://u:secret-pw@127.0.0.1:1/x", connect_args={"connect_timeout": 2}
    )
    health = await check_database(bad, timeout_seconds=3, expected_revision=None)
    await bad.dispose()
    assert health.reachable is False
    assert health.error is not None and "secret-pw" not in health.error
