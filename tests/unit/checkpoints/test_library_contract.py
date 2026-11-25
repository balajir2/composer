"""Pins the parts of langgraph-checkpoint-postgres this codebase depends on.

A dependency bump that changes any of these must come with a code change (and, for
MIGRATIONS, an Alembic revision); this test is where that is noticed first.
"""

from langgraph.checkpoint.postgres import BasePostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def test_migration_count_matches_alembic_0004() -> None:
    assert len(BasePostgresSaver.MIGRATIONS) == 10


def test_async_saver_accepts_a_pool() -> None:
    assert "conn" in AsyncPostgresSaver.__init__.__code__.co_varnames
