"""The official saver's schema version check (fail closed at startup)."""

from typing import Any

from langgraph.checkpoint.postgres import BasePostgresSaver
from psycopg import AsyncConnection

SAVER_MIGRATION_COUNT = len(BasePostgresSaver.MIGRATIONS)


async def assert_saver_schema_current(conn: AsyncConnection[Any]) -> None:
    """Raise unless checkpoint_migrations records exactly the library's migrations.

    A library upgrade that adds a migration needs a matching Alembic revision; until it has one
    the app refuses to start rather than letting the library write to a table it expects to
    look different.
    """
    cur = await conn.execute("SELECT max(v) AS v FROM checkpoint_migrations")
    row: Any = await cur.fetchone()
    found: Any = None
    if row is not None:
        found = row["v"] if isinstance(row, dict) else row[0]
    expected = SAVER_MIGRATION_COUNT - 1
    if found != expected:
        raise RuntimeError(
            f"checkpoint tables are at saver migration {found}, the installed "
            f"langgraph-checkpoint-postgres expects {expected}: add an Alembic revision"
        )
