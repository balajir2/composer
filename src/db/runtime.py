"""Sessions for code that runs outside a request.

The app lifespan registers its sessionmaker here. Executors, startup jobs and
auth dependencies take sessions from it; the CLI builds its own short-lived
engine with ``one_shot_session``.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from src.config import Settings
from src.db.engine import build_engine
from src.db.session import build_sessionmaker

_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def register_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None:
    global _sessionmaker
    _sessionmaker = sm


def unregister_sessionmaker(sm: async_sessionmaker[AsyncSession]) -> None:
    """Clear the registration only if ``sm`` is still the registered one."""
    global _sessionmaker
    if _sessionmaker is sm:
        _sessionmaker = None


def get_registered_sessionmaker() -> async_sessionmaker[AsyncSession] | None:
    return _sessionmaker


def request_sessionmaker(conn: HTTPConnection) -> async_sessionmaker[AsyncSession] | None:
    """The app's sessionmaker, or None when the app runs without a database (unit tests)."""
    return cast(
        "async_sessionmaker[AsyncSession] | None",
        getattr(conn.app.state, "db_sessionmaker", None),
    )


def require_sessionmaker(conn: HTTPConnection) -> async_sessionmaker[AsyncSession]:
    """The app's sessionmaker, or a 503 when the app runs without a database."""
    sessionmaker = request_sessionmaker(conn)
    if sessionmaker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        )
    return sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("no SQLAlchemy sessionmaker registered; is the app lifespan running?")
    async with _sessionmaker() as session:
        yield session


@asynccontextmanager
async def one_shot_session(settings: Settings) -> AsyncGenerator[AsyncSession]:
    """A session on its own non-pooled engine, for CLI commands."""
    engine = build_engine(settings, for_migrations=True)  # NullPool: right for one-shot runs
    try:
        async with build_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()
