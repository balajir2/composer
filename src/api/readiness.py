"""Database-aware readiness endpoint with bounded diagnostic states."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import get_settings
from src.db.health import DatabaseHealth, alembic_head_revision, check_database

router = APIRouter(tags=["system"])
# A Neon scale-to-zero wake can outlast one short attempt, so the database check gets
# one retry.  The attempts plus the backoff total 1.9 s, under Cloud Run's 2 s startup
# probe timeout.
_DATABASE_ATTEMPT_TIMEOUTS_SECONDS = (1.0, 0.8)
_DATABASE_RETRY_BACKOFF_SECONDS = 0.1


async def _check_with_retry(engine: AsyncEngine, expected_revision: str | None) -> DatabaseHealth:
    """Check the database, retrying once if it is unreachable (Neon cold start)."""
    database = await check_database(
        engine,
        timeout_seconds=_DATABASE_ATTEMPT_TIMEOUTS_SECONDS[0],
        expected_revision=expected_revision,
    )
    if database.reachable:
        return database
    await asyncio.sleep(_DATABASE_RETRY_BACKOFF_SECONDS)
    return await check_database(
        engine,
        timeout_seconds=_DATABASE_ATTEMPT_TIMEOUTS_SECONDS[1],
        expected_revision=expected_revision,
    )


@router.get("/ready")
async def readiness(request: Request, response: Response) -> dict[str, Any]:
    """Return readiness state without exposing exception or connection details."""
    state = request.app.state
    engine = getattr(state, "db_engine", None)
    runtime_initialized = getattr(state, "runtime_initialized", False)
    draining = getattr(state, "draining", False)

    database_state = "unavailable"
    schema_state = "unknown"
    if engine is not None:
        try:
            database = await _check_with_retry(engine, alembic_head_revision())
            database_state = "ready" if database.reachable else "unavailable"
            if database.schema_current is not None:
                schema_state = "current" if database.schema_current else "mismatch"
        except Exception:
            # Readiness must never leak the exception's potentially sensitive text.
            database_state = "unavailable"

    runtime_state = "draining" if draining else ("ready" if runtime_initialized else "initializing")
    telemetry_runtime = getattr(state, "telemetry_runtime", None)
    settings = get_settings()
    if not settings.telemetry_enabled:
        telemetry_state = "disabled"
    elif (
        telemetry_runtime is not None
        and telemetry_runtime.tracer_provider is not None
        and telemetry_runtime.meter_provider is not None
    ):
        telemetry_state = "ready"
    else:
        telemetry_state = "degraded"

    ready = database_state == "ready" and schema_state == "current" and runtime_state == "ready"
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "components": {
            "database": database_state,
            "schema": schema_state,
            "runtime": runtime_state,
            "telemetry": telemetry_state,
        },
    }
