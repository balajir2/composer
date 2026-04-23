"""Admin-only test endpoint for built-in tool providers.

Runs the provider's `health_check()` which typically pings the upstream
service with the currently-loaded API key (from Settings / env).
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.security.auth import ensure_admin
from src.storage.db import get_db
from src.tools.base import HealthStatus
from src.tools.registry import UnknownProviderError, get_provider, list_providers

logger = logging.getLogger(__name__)

_HEALTH_CHECK_TIMEOUT_SEC = 8.0

router = APIRouter(prefix="/admin/tools", tags=["admin-tools"])


class ToolSummary(BaseModel):
    name: str
    description: str
    category: str


class ToolTestResponse(BaseModel):
    ok: bool
    message: str
    has_db_key: bool
    has_runtime_key: bool


@router.get("", response_model=list[ToolSummary])
async def list_built_in_tools(
    _admin: str = Depends(ensure_admin),
) -> list[ToolSummary]:  # pyright: ignore[reportUnusedFunction]
    providers = list_providers(category="standard")
    return [
        ToolSummary(name=p.name, description=p.description, category=p.category) for p in providers
    ]


@router.post("/{tool_id}/test-connection", response_model=ToolTestResponse)
async def test_built_in_tool(
    tool_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> ToolTestResponse:  # pyright: ignore[reportUnusedFunction]
    try:
        provider = get_provider(tool_id)
    except UnknownProviderError as exc:
        raise HTTPException(404, str(exc)) from exc

    # Health-check uses Settings (.env / deploy env) — the key currently in use
    # by running workflows.  We also report whether the DB has a key set so the
    # admin can diagnose DB-vs-runtime drift (keys live in Postgres SoT and are
    # synced to deploy env at build time).
    try:
        db_row = await asyncio.wait_for(
            db.llmapikey.find_unique(where={"provider": tool_id}),  # pyright: ignore[reportAttributeAccessIssue]
            timeout=3.0,
        )
        has_db_key = db_row is not None
    except TimeoutError:
        logger.warning("admin-tools DB lookup timed out for provider=%s", tool_id)
        has_db_key = False

    from src.config import get_settings
    from src.tools.base import ApiKeyAuth

    has_runtime_key = False
    if isinstance(provider.auth, ApiKeyAuth):
        has_runtime_key = bool(getattr(get_settings(), provider.auth.settings_field, ""))
    else:
        has_runtime_key = True  # NoAuth / OAuth

    logger.info(
        "admin-tools test-connection: tool=%s has_db_key=%s has_runtime_key=%s",
        tool_id,
        has_db_key,
        has_runtime_key,
    )
    try:
        health = await asyncio.wait_for(provider.health_check(), timeout=_HEALTH_CHECK_TIMEOUT_SEC)
    except TimeoutError:
        health = HealthStatus(
            ok=False,
            message=(
                f"health_check exceeded {_HEALTH_CHECK_TIMEOUT_SEC:.0f}s — "
                "upstream unreachable or very slow."
            ),
        )
    except Exception as exc:
        logger.exception("admin-tools health_check failed for %s", tool_id)
        health = HealthStatus(ok=False, message=f"{type(exc).__name__}: {exc}")
    logger.info(
        "admin-tools test-connection result: tool=%s ok=%s message=%s",
        tool_id,
        health.ok,
        health.message,
    )
    message = health.message
    if not health.ok and has_db_key and not has_runtime_key:
        message = (
            f"{message}. Note: a key is set in the admin UI but not yet loaded "
            "by the running backend. Restart the backend or redeploy so the "
            "DB key syncs into the deployment env."
        )
    return ToolTestResponse(
        ok=health.ok,
        message=message,
        has_db_key=has_db_key,
        has_runtime_key=has_runtime_key,
    )


__all__ = ["router"]
