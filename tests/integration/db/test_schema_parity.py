# compare_metadata does not compare enum labels, CHECK constraints or column order.
# Enum labels are pinned by tests/unit/db/test_models_metadata.py.
from pathlib import Path
from pprint import pformat
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from src.db.models import Base
from src.db.schema_filters import include_name

pytestmark = pytest.mark.integration


def _diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(
        connection,
        opts={
            "include_name": include_name,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    return compare_metadata(context, Base.metadata)


async def test_models_match_the_migrated_database_exactly(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == [], "Models differ from the database; fix the model:\n" + pformat(diff)


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


async def test_database_is_at_the_alembic_head(engine: AsyncEngine) -> None:
    head = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    ).get_current_head()
    async with engine.connect() as conn:
        current = await conn.run_sync(_current_revision)
    assert current == head
