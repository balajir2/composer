"""Database readiness, for the /ready probe (telemetry roadmap Task 4.3).

Errors are reported by exception class name only: never a message, which can
contain a connection string.
"""

import asyncio
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True)
class DatabaseHealth:
    reachable: bool
    schema_current: bool | None  # None: not checked, or the database was unreachable
    error: str | None


@cache
def alembic_head_revision() -> str:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("alembic has no head revision")
    return head


async def check_database(
    engine: AsyncEngine, *, timeout_seconds: float, expected_revision: str | None
) -> DatabaseHealth:
    try:
        async with asyncio.timeout(timeout_seconds), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            if expected_revision is None:
                return DatabaseHealth(reachable=True, schema_current=None, error=None)
            if (await conn.execute(text("SELECT to_regclass('alembic_version')"))).scalar() is None:
                return DatabaseHealth(reachable=True, schema_current=False, error=None)
            current = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            return DatabaseHealth(
                reachable=True, schema_current=current == expected_revision, error=None
            )
    except Exception as exc:
        return DatabaseHealth(reachable=False, schema_current=None, error=type(exc).__name__)
