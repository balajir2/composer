"""Per-execution context — Prisma client available to executors.

LangGraphExecutor sets this before invoking the compiled graph so the
Agent executor (and any future executor that needs DB access) can look it
up without taking db as an __init__ parameter. Every execution gets its
own context; concurrent executions don't share state because we use
contextvars.
"""

from contextvars import ContextVar
from typing import Any

_current_db: ContextVar[Any | None] = ContextVar("_current_db", default=None)


def set_current_db(db: Any) -> None:
    _current_db.set(db)


def get_current_db() -> Any | None:
    return _current_db.get()


__all__ = ["get_current_db", "set_current_db"]
