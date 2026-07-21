"""Composer — FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import __version__
from src.api.admin_deployment_settings import router as admin_deployment_settings_router
from src.api.admin_llm_keys import router as admin_llm_keys_router
from src.api.admin_llm_models import router as admin_llm_models_router
from src.api.admin_tools import router as admin_tools_router
from src.api.admin_users import router as admin_users_router
from src.api.api_keys import router as api_keys_router
from src.api.approval_email import router as approval_email_router
from src.api.auth_common import router as auth_common_router
from src.api.auth_standalone import router as auth_standalone_router
from src.api.cloud_storage_oauth import router as cloud_storage_oauth_router
from src.api.events_ws import router as events_ws_router
from src.api.executions import router as executions_router
from src.api.internal import router as internal_router
from src.api.llm_models import router as llm_models_router
from src.api.llm_models_live import router as llm_models_live_router
from src.api.mcp_servers import oauth_router
from src.api.mcp_servers import router as mcp_servers_router
from src.api.run import router as run_router
from src.api.test_cleanup import router as test_cleanup_router
from src.api.uploads import router as uploads_router
from src.api.users import router as users_router
from src.api.workflows import router as workflows_router
from src.config import get_settings
from src.config_validation import validate_production_config
from src.maintenance.execution_sweeper import start_sweeper, stop_sweeper
from src.security.key_sync import sync_llm_keys_from_db
from src.storage.db import prisma_lifespan

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown hooks."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("Starting Composer v%s in %s mode", __version__, settings.environment)
    # Fail fast rather than silently serving broken behavior (P0-4) — see
    # src/config_validation.py for what this catches and why.
    validate_production_config(settings)

    async with prisma_lifespan(app) as db:
        # Pull admin-UI-managed LLM keys into runtime Settings so workflow
        # execution (src/llm/providers.py reads from settings.*_api_key)
        # can see them.  Env-set values win; DB fills in the blanks.  A
        # revision restart picks up edits made via the admin UI.
        await sync_llm_keys_from_db(db)

        sweeper_task = start_sweeper(
            app,
            db,
            interval_seconds=settings.execution_sweeper_interval_seconds,
            stuck_after_seconds=settings.execution_stuck_after_seconds,
            approval_timeout_hours=settings.approval_wait_timeout_hours,
        )
        try:
            yield
        finally:
            await stop_sweeper(sweeper_task)

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
    elif settings.environment == "development":
        allow_origins = ["*"]
    else:
        # Standalone + production: explicit allowlist from env.  An empty
        # list rejects every cross-origin request, which is the correct
        # default but lethal in practice — warn so misconfiguration is
        # visible at boot instead of as a CORS error in the browser.
        allow_origins = [
            o.strip() for o in settings.composer_frontend_origins.split(",") if o.strip()
        ]
        if not allow_origins:
            logger.warning(
                "cors: COMPOSER_FRONTEND_ORIGINS is empty in standalone+production — "
                "every cross-origin request from a browser will be rejected. "
                "Set the env var to a comma-separated list of frontend origins."
            )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(admin_deployment_settings_router)
    app.include_router(admin_llm_keys_router)
    app.include_router(admin_llm_models_router)
    app.include_router(admin_tools_router)
    app.include_router(admin_users_router)
    app.include_router(api_keys_router)
    app.include_router(approval_email_router)
    app.include_router(cloud_storage_oauth_router)
    app.include_router(llm_models_router)
    app.include_router(llm_models_live_router)
    app.include_router(workflows_router)
    app.include_router(executions_router)
    app.include_router(events_ws_router)
    app.include_router(internal_router)
    app.include_router(mcp_servers_router)
    app.include_router(oauth_router)
    app.include_router(run_router)
    app.include_router(uploads_router)
    app.include_router(users_router)

    # Test-only self-service hard-delete for e2e accounts (never in
    # production) -- see src/api/test_cleanup.py's module docstring.
    if settings.environment != "production":
        app.include_router(test_cleanup_router)

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
