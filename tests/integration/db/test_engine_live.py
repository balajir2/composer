import os

import pytest
from sqlalchemy import text

from src.config import Settings
from src.db.engine import build_engine, connect_with_retry

pytestmark = pytest.mark.integration


async def test_engine_connects_without_server_side_prepared_statements() -> None:
    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"])
    engine = build_engine(settings)
    try:
        await connect_with_retry(engine, settings)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
            raw = await conn.get_raw_connection()
            assert raw.driver_connection is not None
            assert raw.driver_connection.prepare_threshold is None
    finally:
        await engine.dispose()
