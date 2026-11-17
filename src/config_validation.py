"""Fail-fast execution timing and production configuration checks (P0-4).

Execution timing is checked at app creation in every environment;
production-only checks run at startup so a misconfigured deployment
refuses to serve broken behavior. Added after the 2026-07-11 incident where
BACKEND_PUBLIC_URL/FRONTEND_URL were never set in production —
approval/reset emails silently linked to http://localhost and nothing
failed until a human clicked a broken link.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings

_LOCALHOST_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
_DEV_JWT_SECRET = "dev-only-not-for-production"
_DEV_DATABASE_URL = "postgresql://composer:composer@localhost:5432/composer"


class ProductionConfigError(RuntimeError):
    """Raised when required configuration is missing or unsafe."""


def _is_localhost_url(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _LOCALHOST_MARKERS)


def validate_execution_timing(settings: Settings) -> None:
    """Reject processing budgets that cannot fit within a Cloud Tasks delivery."""
    problems: list[str] = []
    if settings.execution_max_runtime_seconds <= 0:
        problems.append("EXECUTION_MAX_RUNTIME_SECONDS must be greater than 0.")
    if not 1 <= settings.execution_shutdown_grace_seconds <= 60:
        problems.append("EXECUTION_SHUTDOWN_GRACE_SECONDS must be between 1 and 60.")
    if settings.execution_dispatch_deadline_seconds > 1800:
        problems.append(
            "EXECUTION_MAX_RUNTIME_SECONDS + EXECUTION_SHUTDOWN_GRACE_SECONDS "
            "must not exceed 1800 seconds."
        )
    if problems:
        raise ProductionConfigError(
            "Refusing to start: invalid execution timing —\n- " + "\n- ".join(problems)
        )


def validate_production_config(settings: Settings) -> None:
    """Raise ProductionConfigError listing every problem found, if any.

    Execution timing is checked in all environments. Production additionally
    rejects localhost URLs and dev secrets, which remain valid locally.
    """
    validate_execution_timing(settings)
    if settings.environment != "production":
        return

    problems: list[str] = []

    if _is_localhost_url(settings.backend_public_url):
        problems.append(
            f"BACKEND_PUBLIC_URL={settings.backend_public_url!r} points at localhost — "
            "emailed approve/reject links would resolve to a developer's machine "
            "instead of the deployed backend."
        )
    if _is_localhost_url(settings.frontend_url):
        problems.append(
            f"FRONTEND_URL={settings.frontend_url!r} points at localhost — post-"
            "decision redirects and password-reset links would resolve to a "
            "developer's machine instead of the deployed frontend."
        )
    if settings.jwt_secret == _DEV_JWT_SECRET:
        problems.append("JWT_SECRET is still the development default — set a real secret.")
    if not settings.database_url or settings.database_url == _DEV_DATABASE_URL:
        problems.append("DATABASE_URL is unset or still the local development default.")
    if not settings.encryption_key:
        problems.append("ENCRYPTION_KEY is unset — stored secrets cannot be encrypted.")
    if not settings.cloud_tasks_service_account:
        problems.append(
            "CLOUD_TASKS_SERVICE_ACCOUNT is unset — /internal/* (claim-and-run, sweep, "
            "poll-file-triggers) cannot authenticate Cloud Tasks/Scheduler callers."
        )

    if problems:
        raise ProductionConfigError(
            "Refusing to start: unsafe production configuration —\n- " + "\n- ".join(problems)
        )


__all__ = ["ProductionConfigError", "validate_execution_timing", "validate_production_config"]
