"""Composer — FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import __version__
from src.api.admin_llm_keys import router as admin_llm_keys_router
from src.api.auth_common import router as auth_common_router
from src.api.auth_standalone import router as auth_standalone_router
from src.api.events import router as events_router
from src.api.executions import router as executions_router
from src.api.mcp_servers import oauth_router
from src.api.mcp_servers import router as mcp_servers_router
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

    # ADR-0014: fail fast if embedded mode is misconfigured
    if settings.deployment_mode == "embedded":
        if not settings.iep_jwt_issuer:
            raise RuntimeError(
                "COMPOSER_DEPLOYMENT_MODE=embedded requires IEP_JWT_ISSUER "
                "(expected `iss` claim in IEP-issued JWTs)"
            )
        if not (settings.iep_shared_secret or settings.iep_jwks_url):
            raise RuntimeError(
                "COMPOSER_DEPLOYMENT_MODE=embedded requires IEP_SHARED_SECRET "
                "or IEP_JWKS_URL (at least one JWT verification method)"
            )

    # ADR-0015: warn loudly if dev-mode auth fallback is enabled
    if settings.environment == "development":
        logger.warning(
            "auth: dev-mode fallback ENABLED (user_id='dev' when no Authorization header). "
            "Set ENVIRONMENT=production to require real auth."
        )

    app = FastAPI(
        title="Composer",
        description="Python rebuild of Open Agent Builder on IE-compatible stack.",
        version=__version__,
        lifespan=lifespan,
    )

    # Mode-aware CORS (Phase 7a)
    if settings.deployment_mode == "embedded":
        allow_origins = [settings.iep_ui_origin] if settings.iep_ui_origin else []
    else:
        allow_origins = ["*"] if settings.environment == "development" else []

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(admin_llm_keys_router)
    app.include_router(workflows_router)
    app.include_router(executions_router)
    app.include_router(events_router)
    app.include_router(mcp_servers_router)
    app.include_router(oauth_router)

    # Phase 7a: auth
    if settings.deployment_mode == "standalone":
        app.include_router(auth_standalone_router)
    app.include_router(auth_common_router)

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
