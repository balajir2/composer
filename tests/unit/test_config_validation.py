"""Tests for fail-fast production configuration validation (P0-4).

The 2026-07-11 incident this guards against: BACKEND_PUBLIC_URL and
FRONTEND_URL were never set in production, so approval/reset emails
silently linked to http://localhost — the app looked fully deployed and
nothing failed until a human clicked a broken link.
"""

from typing import Any

import pytest

from src.config import Settings
from src.config_validation import ProductionConfigError, validate_production_config


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "production",
        "backend_public_url": "https://api.example.com",
        "frontend_url": "https://www.example.com",
        "jwt_secret": "a-real-production-secret-not-the-dev-default",
        "database_url": "postgresql://user:pass@prod-host:5432/composer",
        "encryption_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }
    base.update(overrides)
    return Settings(**base)  # pyright: ignore[reportCallIssue]


def test_fully_configured_production_settings_pass() -> None:
    validate_production_config(_settings())


def test_development_environment_skips_all_checks() -> None:
    """Dev defaults (localhost URLs, dev JWT secret) are expected locally
    — only environment == "production" enforces anything."""
    validate_production_config(
        Settings(environment="development")  # pyright: ignore[reportCallIssue]
    )


def test_localhost_backend_public_url_blocks_startup() -> None:
    with pytest.raises(ProductionConfigError, match="BACKEND_PUBLIC_URL"):
        validate_production_config(_settings(backend_public_url="http://localhost:8000"))


def test_localhost_frontend_url_blocks_startup() -> None:
    with pytest.raises(ProductionConfigError, match="FRONTEND_URL"):
        validate_production_config(_settings(frontend_url="http://localhost:3000"))


def test_dev_jwt_secret_blocks_startup() -> None:
    with pytest.raises(ProductionConfigError, match="JWT_SECRET"):
        validate_production_config(_settings(jwt_secret="dev-only-not-for-production"))


def test_dev_database_url_blocks_startup() -> None:
    with pytest.raises(ProductionConfigError, match="DATABASE_URL"):
        validate_production_config(
            _settings(database_url="postgresql://composer:composer@localhost:5432/composer")
        )


def test_missing_encryption_key_blocks_startup() -> None:
    with pytest.raises(ProductionConfigError, match="ENCRYPTION_KEY"):
        validate_production_config(_settings(encryption_key=""))


def test_multiple_problems_all_reported_together() -> None:
    """Report every problem in one error, not just the first — an admin
    fixing one env var shouldn't have to redeploy repeatedly to discover
    the next missing one."""
    with pytest.raises(ProductionConfigError) as exc_info:
        validate_production_config(
            _settings(
                backend_public_url="http://localhost:8000",
                frontend_url="http://localhost:3000",
            )
        )
    message = str(exc_info.value)
    assert "BACKEND_PUBLIC_URL" in message
    assert "FRONTEND_URL" in message


def test_google_oauth_settings_default_to_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import Settings

    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_PICKER_API_KEY", raising=False)
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.google_oauth_client_id == ""
    assert settings.google_oauth_client_secret == ""
    assert settings.google_picker_api_key == ""
