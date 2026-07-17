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
    # Shared by the admin-forced reset (ADR-0024) and self-service email reset
    # (ADR-0027) — 30 min balances the admin-handoff case (interactive, could
    # be shorter) against the email case (user needs time to check their inbox).
    jwt_password_change_ttl_seconds: int = 1800  # 30 minutes

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
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")
    qwen_api_key: str = Field(default="", description="Alibaba DashScope/Qwen API key")
    dashscope_api_key: str = Field(default="", description="Alibaba DashScope API key")
    siliconflow_api_key: str = Field(default="", description="SiliconFlow API key")
    zhipu_api_key: str = Field(default="", description="Zhipu/BigModel API key")
    cohere_api_key: str = Field(default="", description="Cohere API key")
    jina_api_key: str = Field(default="", description="Jina AI API key")
    voyage_api_key: str = Field(default="", description="Voyage AI API key")
    pinecone_inference_api_key: str = Field(default="", description="Pinecone Inference API key")

    # ─── Agent tools (Phase 2) ────────────────────
    tavily_api_key: str = Field(default="", description="Tavily web-search API key")
    serper_api_key: str = Field(default="", description="Serper.dev Google-search API key")
    firecrawl_api_key: str = Field(default="", description="Firecrawl web-scrape API key")
    browserless_api_key: str = Field(default="", description="Browserless headless-Chrome API key")

    # ─── Gamma-AI (Phase 6c) ─────────────────────
    gamma_api_key: str = Field(default="", description="Gamma.app public API key.")
    resend_api_key: str = Field(default="", description="Resend API key for email delivery.")
    resend_from_email: str = Field(
        default="noreply@script-research.online",
        description="Verified Resend sender address for transactional emails (password reset, etc).",
    )

    # ─── Google Drive OAuth (file-trigger cloud storage, 2026-07-15) ──
    google_oauth_client_id: str = Field(
        default="", description="Google Cloud OAuth 2.0 Client ID (Web application type)."
    )
    google_oauth_client_secret: str = Field(
        default="",
        description="Google Cloud OAuth 2.0 Client Secret, paired with google_oauth_client_id.",
    )
    google_picker_api_key: str = Field(
        default="",
        description="Google Cloud API key restricted to the Picker API, used by the frontend "
        "to embed the Drive folder picker (separate from the OAuth client credentials).",
    )

    # ─── Jira (Phase 6f) ──────────────────────────
    jira_domain: str = Field(
        default="", description="Jira Cloud domain (e.g. your-org.atlassian.net)."
    )
    jira_email: str = Field(default="", description="Jira Cloud account email for Basic auth.")
    jira_api_token: str = Field(
        default="",
        description="Jira Cloud API token (from https://id.atlassian.com/manage/api-tokens).",
    )

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

    # ─── Backend-constructed links back to the frontend ─────
    frontend_url: str = Field(
        default="http://localhost:3000",
        description=(
            "Base URL of the Composer frontend, used to build absolute links "
            "in backend-sent emails (e.g. password-reset links). Production "
            "sets this to https://www.flowcomposer.online."
        ),
    )

    # ─── Approve-via-email (2026-07-11) ──────────
    approval_link_ttl_hours: int = Field(
        default=72,
        description=(
            "How long an emailed approve/reject link stays valid. After it "
            "expires, in-app approval (POST /executions/{id}/resume) still "
            "works — this only bounds the emailed shortcut."
        ),
    )
    backend_public_url: str = Field(
        default="http://localhost:8000",
        description=(
            "Public base URL of THIS backend (not the frontend) — used to "
            "build emailed approve/reject links, which must resolve directly "
            "to the backend since they trigger a server-side resume. "
            "Production sets this to the Cloud Run backend service URL."
        ),
    )
    approval_wait_timeout_hours: int = Field(
        default=168,
        description=(
            "Auto-fail a waiting_approval execution after this many hours "
            "with no decision, independent of the emailed link's own shorter "
            "TTL — bounds Postgres/checkpoint row growth from runs nobody "
            "ever approves or rejects."
        ),
    )
    approval_attachment_root: str = Field(
        default="/tmp/composer-attachments",
        description=(
            "Filesystem root a user-approval node's attachmentPath must "
            "resolve inside of. attachmentPath is substituted from workflow "
            "state (typically {{lastOutput}} from an upstream file-write "
            "node), so it's treated the same as any other state-substituted "
            "value that reaches a filesystem read: bounded to a known-safe "
            "root rather than trusted to point anywhere readable on the "
            "server. A path outside this root is skipped (email still "
            "sends, without the attachment) rather than read."
        ),
    )
    approval_attachment_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        description=(
            "Max size of a file attached to an approval email. Larger files "
            "are skipped (email still sends, without the attachment) rather "
            "than inflating the email payload or blowing up memory on a "
            "base64-encode of an unexpectedly huge file."
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

    # ─── HTTP node SSRF policy (P0-6) ──────────────
    ssrf_protection_enabled: bool = Field(
        default=True,
        description=(
            "Block the HTTP node from reaching loopback/private/link-local/"
            "metadata addresses. Only disable for a fully trusted, "
            "single-tenant deployment."
        ),
    )
    http_node_allow_http: bool = Field(
        default=False,
        description="Allow plain http:// (not just https://) for the HTTP node.",
    )
    http_node_hostname_allowlist: str = Field(
        default="",
        description=(
            "Comma-separated hostnames (lowercase, exact match) the HTTP node "
            "may reach even though they'd otherwise be blocked — e.g. a "
            "private VPC service an administrator intentionally wants "
            "workflows to call. Same comma-separated convention as "
            "COMPOSER_FRONTEND_ORIGINS."
        ),
    )
    http_node_max_response_bytes: int = Field(
        default=5_000_000,
        description="Max bytes buffered from an HTTP node response before aborting.",
    )

    # ─── Execution-input boundary (P0-7) ───────────
    # Reserved-key rejection (finalOutput, _-prefixed engine bookkeeping)
    # is always on. This flag is the stricter, opt-in layer: reject ANY
    # execution-input key not declared on the workflow's Start node.
    # Default off — many existing workflows intentionally rely on
    # undeclared caller-supplied variables, so this is a deliberate
    # per-deployment choice, not a silent behavior change.
    strict_execution_input_enabled: bool = Field(
        default=False,
        description=(
            "Reject execution input containing any variable not declared on "
            "the workflow's Start node. Off by default for backward "
            "compatibility."
        ),
    )

    # ─── Rate limits (Phase 8) ────────────────────
    rate_limit_executions_per_minute: int = 30
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_minute: int = 5
    rate_limit_refresh_per_minute: int = 30
    rate_limit_change_password_per_minute: int = 10
    rate_limit_resume_per_minute: int = 60
    rate_limit_mcp_test_per_minute: int = 10
    rate_limit_picker_token_per_minute: int = 10
    rate_limit_api_run_per_minute: int = 60
    rate_limit_users_search_per_minute: int = 30
    rate_limit_forgot_password_per_minute: int = 5
    # Higher than forgot-password's 5/min: legitimate approve/reject clicking
    # plus headroom for transient email-security-scanner prefetches (e.g.
    # Microsoft Safe Links) both need to fit without tripping the limiter.
    rate_limit_approval_email_per_minute: int = 20

    # ─── Cloud Tasks (P1-2 durable execution) ──────────────────────
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    cloud_tasks_queue: str = "composer-executions"
    # Service account Cloud Tasks uses to mint the OIDC token it presents
    # to the claim-and-run endpoint. Empty in dev (auth skipped when unset
    # — see src/api/internal.py's _verify_internal_oidc).
    cloud_tasks_service_account: str = ""
    # DESIGN DECISION (P1-2, "lease and heartbeat behavior"): rather than a
    # short lease with periodic mid-execution heartbeat renewal (the usual
    # pattern for long batch jobs), claim-and-run (src/api/internal.py) runs
    # an execution to completion synchronously within ONE bounded Cloud
    # Tasks-delivered HTTP request. The lease is sized to match — not
    # exceed — that request's own maximum duration (Cloud Run's configured
    # request timeout), so it needs no separate renewal: the lease *is* the
    # heartbeat, because "the request is still running" and "the lease is
    # still valid" cover the same span by construction. If Cloud Run's
    # request timeout for the service ever changes, this must change with
    # it — keep them equal, don't drift.
    execution_lease_seconds: int = 3600
    # How many times sweep_expired_leases will re-enqueue a fresh Cloud
    # Task for the same execution before giving up and dead-lettering it
    # (marking `failed` rather than retrying indefinitely).
    execution_max_delivery_attempts: int = 5
    # P1-4 ("add bounded retention and cleanup"): sweep_old_execution_events
    # deletes execution_events rows older than this. Event rows carry no
    # operational state (only SSE/WebSocket replay history), so unbounded
    # growth is pure Postgres storage/index bloat with no functional
    # upside — 30 days comfortably covers any realistic "what happened on
    # that run" investigation window.
    execution_events_retention_days: int = 30

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
