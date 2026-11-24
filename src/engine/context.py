"""Per-execution context — Prisma client available to executors.

LangGraphExecutor sets this before invoking the compiled graph so the
Agent executor (and any future executor that needs DB access) can look it
up without taking db as an __init__ parameter. Every execution gets its
own context; concurrent executions don't share state because we use
contextvars.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.engine.events_pg import PostgresEventStore

_current_sessionmaker: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "_current_sessionmaker", default=None
)


def set_current_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None:
    _current_sessionmaker.set(sm)


def get_current_sessionmaker() -> async_sessionmaker[AsyncSession] | None:
    return _current_sessionmaker.get()


@dataclass(frozen=True)
class LangSmithConfig:
    """Per-execution LangSmith tracing config.

    Threaded explicitly through BuildContext + build_chat_model so that
    tracing doesn't silently break when env vars are stripped (fix #6).
    """

    tracing_v2: bool
    project: str
    endpoint: str
    api_key: str


_current_langsmith: ContextVar[LangSmithConfig | None] = ContextVar(
    "_current_langsmith", default=None
)


def set_current_langsmith(config: LangSmithConfig | None) -> None:
    _current_langsmith.set(config)


def get_current_langsmith() -> LangSmithConfig | None:
    return _current_langsmith.get()


_current_execution_id: ContextVar[str | None] = ContextVar("_current_execution_id", default=None)
_current_event_bus: ContextVar[PostgresEventStore | None] = ContextVar(
    "_current_event_bus", default=None
)


def set_current_execution_id(execution_id: str | None) -> None:
    _current_execution_id.set(execution_id)


def get_current_execution_id() -> str | None:
    return _current_execution_id.get()


def set_current_event_bus(bus: PostgresEventStore | None) -> None:
    _current_event_bus.set(bus)


def get_current_event_bus() -> PostgresEventStore | None:
    return _current_event_bus.get()


__all__ = [
    "LangSmithConfig",
    "get_current_event_bus",
    "get_current_execution_id",
    "get_current_langsmith",
    "get_current_sessionmaker",
    "set_current_event_bus",
    "set_current_execution_id",
    "set_current_langsmith",
    "set_current_sessionmaker",
]
