"""Runtime configuration loaded from environment variables.

All secrets and environment-specific settings are read via Pydantic Settings.
This keeps config typed, validated, and documented in one place.
"""

from functools import lru_cache
from typing import Literal

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

    # ─── LLM providers (Phase 2) ──────────────────
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    openai_api_key: str = Field(default="", description="OpenAI API key")
    google_api_key: str = Field(default="", description="Google AI Studio API key")
    groq_api_key: str = Field(default="", description="Groq API key")

    # ─── Agent tools (Phase 2) ────────────────────
    tavily_api_key: str = Field(default="", description="Tavily web-search API key")
    serper_api_key: str = Field(default="", description="Serper.dev Google-search API key")
    firecrawl_api_key: str = Field(default="", description="Firecrawl web-scrape API key")
    browserless_api_key: str = Field(default="", description="Browserless headless-Chrome API key")

    # ─── Deployment mode (Phase 7a, ADR-0014) ────────
    deployment_mode: Literal["standalone", "embedded"] = Field(
        default="standalone",
        validation_alias="COMPOSER_DEPLOYMENT_MODE",
        description="'standalone' owns users + issues its own JWTs; 'embedded' trusts JWTs from IEP.",
    )

    # ─── Embedded-mode (IEP integration) ─────────────
    iep_jwt_issuer: str = Field(default="", description="Expected `iss` claim in IEP-issued JWTs.")
    iep_jwks_url: str = Field(
        default="", description="RS256: URL to fetch IEP's public keys (Phase 7b)."
    )
    iep_shared_secret: str = Field(
        default="", description="HS256: shared secret with IEP (Phase 7a path)."
    )
    iep_ui_origin: str = Field(
        default="", description="Exact origin allowed by CORS when embedded."
    )

    # ─── Standalone password hashing ─────────────────
    bcrypt_rounds: int = Field(default=12, description="bcrypt cost factor.")


@lru_cache
def get_settings() -> Settings:
    """Cached singleton settings instance."""
    return Settings()
