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

# Migrations are a one-shot step, not a crash-loop-prone service, so they get
# a longer connect-timeout floor than app startup. SQLAlchemy's first
# connection runs extra setup round trips (~4 s from a distant region), and
# the app's 3 s default exists to stop Cloud Run crash loops, which doesn't
# apply to a migration run. Worst case, a database that is down delays the
# deploy failure by about attempts x 15 s (~45 s with the default 3 attempts),
# which is acceptable for a one-shot step.
MIGRATION_CONNECT_TIMEOUT_FLOOR_SECONDS = 15.0

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
        # Prisma always forced `public`, and Alembic now builds fresh databases on its own.
        # A "$user" schema (one named after the role) must not capture the version table or
        # any revision's tables, so pin `public` for the whole session. Session-level (not
        # LOCAL) so the version table and every revision see it. Nothing above this line
        # queries the database, so the pin precedes the first catalog lookup.
        connection.execute(text("SET search_path TO public"))
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        context.run_migrations()


async def _run_online() -> None:
    settings = get_settings()
    migration_settings = settings.model_copy(
        update={
            "db_connect_timeout_seconds": max(
                settings.db_connect_timeout_seconds, MIGRATION_CONNECT_TIMEOUT_FLOOR_SECONDS
            )
        }
    )
    engine = build_engine(migration_settings, for_migrations=True)
    try:
        # A Neon cold start must not fail a deploy.
        await connect_with_retry(engine, migration_settings)
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
