"""Start and stop the SQLAlchemy engine with the FastAPI app."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import get_settings
from src.db.engine import build_engine, connect_with_retry
from src.db.session import build_sessionmaker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def sqlalchemy_lifespan(app: FastAPI) -> AsyncGenerator[AsyncEngine, None]:
    """Connect the engine and attach it, plus a sessionmaker, to ``app.state``."""
    settings = get_settings()
    engine = build_engine(settings)
    try:
        await connect_with_retry(engine, settings)
        app.state.db_engine = engine
        app.state.db_sessionmaker = build_sessionmaker(engine)
        yield engine
    finally:
        for name in ("db_sessionmaker", "db_engine"):
            if hasattr(app.state, name):
                delattr(app.state, name)
        try:
            await engine.dispose()
        except Exception:
            # Never let a dispose failure mask the original error.
            logger.exception("sqlalchemy: engine dispose failed")
