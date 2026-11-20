"""Alembic environment: runs migrations through Composer's async engine."""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.config import get_settings
from src.db.engine import build_engine, connect_with_retry
from src.db.models import Base
from src.db.schema_filters import include_name

# Serializes concurrent `alembic upgrade` runs (e.g. two deploys); the
# transaction-level advisory lock is released at commit.
MIGRATION_LOCK_KEY = 7_311_190_457

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_name=include_name,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        context.run_migrations()


async def _run_online() -> None:
    settings = get_settings()
    engine = build_engine(settings, for_migrations=True)
    try:
        # A Neon cold start must not fail a deploy.
        await connect_with_retry(engine, settings)
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported; run against a database.")

if sys.platform == "win32":
    # psycopg's async mode cannot use Windows' default Proactor event loop.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_run_online())
else:
    asyncio.run(_run_online())
