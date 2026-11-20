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

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@dataclass(frozen=True)
class DatabaseHealth:
    reachable: bool
    schema_current: bool | None  # None when no expected revision was given or it was unreachable
    error: str | None


@cache
def alembic_head_revision() -> str:
    head = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()
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
            current = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            return DatabaseHealth(
                reachable=True, schema_current=current == expected_revision, error=None
            )
    except Exception as exc:
        return DatabaseHealth(reachable=False, schema_current=None, error=type(exc).__name__)
