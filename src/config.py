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
    # Access token: 8h — enterprise norm (balance between re-auth friction and
    # blast-radius of a leaked token).  Rotated transparently by the NextAuth
    # JWT callback well before expiry, so users never see 401 in practice.
    jwt_access_ttl_seconds: int = 28800  # 8 hours
    # Refresh token: 30 days.  Idle users must re-auth after this window;
    # active users rotate their refresh token on every /auth/refresh call,
    # so continuous activity extends the window indefinitely.
    jwt_refresh_ttl_seconds: int = 2592000  # 30 days

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

    # ─── Gamma-AI (Phase 6c) ─────────────────────
    gamma_api_key: str = Field(default="", description="Gamma.app public API key.")

    # ─── Arcade (Phase 6d) ────────────────────────
    arcade_api_key: str = Field(default="", description="Arcade.dev API key.")

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

    # ─── Standalone-mode CORS (production) ───────────
    composer_frontend_origins: str = Field(
        default="",
        description=(
            "Comma-separated list of frontend origins allowed by CORS when "
            "deployment_mode='standalone' and environment='production'. "
            "Each entry must be an exact scheme+host+port match, e.g. "
            "'https://composer.example.com,https://composer-frontend-xyz-uc.a.run.app'. "
            "Empty in production rejects every cross-origin request."
        ),
    )

    # ─── Standalone password hashing ─────────────────
    bcrypt_rounds: int = Field(default=12, description="bcrypt cost factor.")

    # ─── Size caps (Phase 8) ──────────────────────
    max_workflow_nodes: int = Field(default=100, description="Max nodes per workflow.")
    max_workflow_edges: int = Field(default=200, description="Max edges per workflow.")
    max_execution_input_bytes: int = Field(
        default=1_000_000,
        description="Max bytes for POST /executions input (JSON-serialized).",
    )

    # ─── Rate limits (Phase 8) ────────────────────
    rate_limit_executions_per_minute: int = 30
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_minute: int = 5
    rate_limit_refresh_per_minute: int = 30
    rate_limit_resume_per_minute: int = 60
    rate_limit_mcp_test_per_minute: int = 10
    rate_limit_api_run_per_minute: int = 60

    # ─── Stuck-execution sweeper ─────────────────
    # Any WorkflowExecution row in 'running' for longer than this without a
    # terminal status update is treated as crashed (worker died, runtime
    # timed out, network blip during persist).  See
    # docs/archive/incident-history/2026-04-30-execution-status-truth.md.
    execution_stuck_after_seconds: int = Field(
        default=900,
        description=(
            "Mark 'running' executions as failed once they've been running this "
            "long without a terminal update. Default 15 min covers the longest "
            "legitimate workflows; tighten if your runtime has a shorter "
            "request budget (Vercel ~5min, Lambda ~15min)."
        ),
    )
    execution_sweeper_interval_seconds: int = Field(
        default=300,
        description="How often the background sweeper runs. Set 0 to disable.",
    )

    # ─── SSO (Phase 10a) ────────────────────────
    sso_enabled: bool = Field(default=False, description="Enable /auth/sso-exchange endpoint.")
    sso_azure_ad_tenant_id: str = Field(
        default="",
        description="Azure AD tenant ID; required when sso_enabled=True.",
    )
    sso_azure_ad_expected_audience: str = Field(
        default="",
        description="Expected 'aud' claim in Azure-issued JWTs.",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached singleton settings instance."""
    return Settings()
