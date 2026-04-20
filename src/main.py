"""Composer — FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import __version__
from src.api.executions import router as executions_router
from src.api.workflows import router as workflows_router
from src.config import get_settings
from src.storage.db import prisma_lifespan

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown hooks."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("Starting Composer v%s in %s mode", __version__, settings.environment)

    async with prisma_lifespan(app):
        yield

    logger.info("Shutting down Composer")


def create_app() -> FastAPI:
    """Application factory. Keeps tests and scripts free to construct their own."""
    settings = get_settings()

    app = FastAPI(
        title="Composer",
        description="Python rebuild of Open Agent Builder on IE-compatible stack.",
        version=__version__,
        lifespan=lifespan,
    )

    # CORS: permissive in dev, explicit allowlist in prod (tightened in later phases)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(workflows_router)
    app.include_router(executions_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        """Liveness probe. Always returns ok if the process is responding."""
        return {
            "status": "ok",
            "service": "composer",
            "version": __version__,
            "environment": settings.environment,
        }

    return app


app = create_app()
