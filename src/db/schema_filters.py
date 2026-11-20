"""What Alembic compares and migrates."""

from collections.abc import Mapping
from typing import Any

# Bookkeeping tables owned by the migration tools themselves.
IGNORED_TABLES = frozenset({"_prisma_migrations", "alembic_version"})


def include_name(name: str | None, type_: str, parent_names: Mapping[Any, str | None]) -> bool:
    """Alembic ``include_name`` hook: skip the tools' own bookkeeping tables."""
    if type_ == "table":
        return name not in IGNORED_TABLES
    return True
