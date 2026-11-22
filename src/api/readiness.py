"""Database-aware readiness endpoint with bounded diagnostic states."""

from typing import Any

from fastapi import APIRouter, Request, Response

from src.config import get_settings
from src.db.health import alembic_head_revision, check_database

router = APIRouter(tags=["system"])
_DATABASE_TIMEOUT_SECONDS = 1.0


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
            database = await check_database(
                engine,
                timeout_seconds=_DATABASE_TIMEOUT_SECONDS,
                expected_revision=alembic_head_revision(),
            )
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
