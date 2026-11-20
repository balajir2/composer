"""Start and stop the SQLAlchemy engine with the FastAPI app."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import get_settings
from src.db.engine import build_engine, connect_with_retry
from src.db.session import build_sessionmaker


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
        await engine.dispose()
