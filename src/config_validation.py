"""Fail-fast production configuration checks (P0-4).

Called once at startup when environment == "production" so a
misconfigured deployment refuses to start instead of silently serving
broken behavior. Added after the 2026-07-11 incident where
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
    """Raised when required production configuration is missing or unsafe."""


def _is_localhost_url(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _LOCALHOST_MARKERS)


def validate_production_config(settings: Settings) -> None:
    """Raise ProductionConfigError listing every problem found, if any.

    No-op unless settings.environment == "production" — localhost URLs
    and the dev JWT secret are the expected, correct configuration for
    local development.
    """
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

    if problems:
        raise ProductionConfigError(
            "Refusing to start: unsafe production configuration —\n- " + "\n- ".join(problems)
        )


__all__ = ["ProductionConfigError", "validate_production_config"]
