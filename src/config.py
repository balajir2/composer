"""Runtime configuration loaded from environment variables.

All secrets and environment-specific settings are read via Pydantic Settings.
This keeps config typed, validated, and documented in one place.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. See .env.example for the full list."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ─── App ──────────────────────────────────────
    app_name: str = "composer"
    environment: str = Field(default="development", description="development | production")
    log_level: str = "INFO"

    # ─── Database ─────────────────────────────────
    database_url: str = Field(
        default="postgresql://composer:composer@localhost:5432/composer",
        description="Postgres connection string. Required.",
    )

    # ─── Auth ─────────────────────────────────────
    jwt_secret: str = Field(
        default="dev-only-not-for-production",
        description="JWT signing secret. Rotate for production.",
    )
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 3600  # 1 hour
    jwt_refresh_ttl_seconds: int = 604800  # 7 days

    # ─── Encryption (AES-256-GCM for secrets at rest) ─────
    encryption_key: str = Field(
        default="",
        description="32-byte base64 key for encrypting stored secrets.",
    )

    # ─── LangSmith tracing (optional) ──────────────
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "composer"
    langchain_endpoint: str = "https://api.smith.langchain.com"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton settings instance."""
    return Settings()
