"""Alembic environment: runs migrations through Composer's async engine."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from src.config import get_settings
from src.db.engine import build_engine
from src.db.models import Base
from src.db.schema_filters import include_name

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_name=include_name,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = build_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported; run against a database.")

asyncio.run(_run_online())
