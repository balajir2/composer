"""What Alembic compares and migrates."""

from collections.abc import Mapping
from typing import Any

# Alembic's own bookkeeping table. (``_prisma_migrations`` was ignored until revision 0006
# dropped it.)
IGNORED_TABLES = frozenset({"alembic_version"})


def include_name(name: str | None, type_: str, parent_names: Mapping[Any, str | None]) -> bool:
    """Alembic ``include_name`` hook: skip Alembic's own bookkeeping table."""
    if type_ == "table":
        return name not in IGNORED_TABLES
    return True
