"""Tests for Settings fields added in Phase 7a."""

import pytest


def test_deployment_mode_defaults_standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSER_DEPLOYMENT_MODE", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().deployment_mode == "standalone"


def test_deployment_mode_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().deployment_mode == "embedded"


def test_deployment_mode_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "cloud")
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        get_settings()


def test_bcrypt_rounds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BCRYPT_ROUNDS", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().bcrypt_rounds == 12


def test_iep_settings_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["IEP_JWT_ISSUER", "IEP_JWKS_URL", "IEP_SHARED_SECRET", "IEP_UI_ORIGIN"]:
        monkeypatch.delenv(name, raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    assert settings.iep_jwt_issuer == ""
    assert settings.iep_jwks_url == ""
    assert settings.iep_shared_secret == ""
    assert settings.iep_ui_origin == ""


def test_composer_frontend_origins_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPOSER_FRONTEND_ORIGINS", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().composer_frontend_origins == ""


def test_composer_frontend_origins_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "COMPOSER_FRONTEND_ORIGINS",
        "https://a.example.com,https://b.example.com",
    )
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().composer_frontend_origins == "https://a.example.com,https://b.example.com"


def test_cloud_tasks_settings_have_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("CLOUD_TASKS_QUEUE", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.gcp_project_id == ""
    assert settings.gcp_region == "us-central1"
    assert settings.cloud_tasks_queue == "composer-executions"
    assert settings.cloud_tasks_service_account == ""
    assert settings.execution_lease_seconds == 1770


@pytest.fixture
def clean_timing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "EXECUTION_MAX_RUNTIME_SECONDS",
        "EXECUTION_SHUTDOWN_GRACE_SECONDS",
        "ORPHANED_RUNNING_GRACE_SECONDS",
        "EXECUTION_DISPATCH_DEADLINE_SECONDS",
        "EXECUTION_LEASE_SECONDS",
        "APPROVAL_WAIT_TIMEOUT_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_timing_environment")
def test_execution_deadline_defaults() -> None:
    from src.config import Settings

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.execution_max_runtime_seconds == 1740
    assert settings.execution_shutdown_grace_seconds == 30
    assert settings.execution_dispatch_deadline_seconds == 1770
    assert settings.execution_lease_seconds == 1770
    assert settings.orphaned_running_grace_seconds == 120
    assert settings.approval_wait_timeout_hours == 0


@pytest.mark.usefixtures("clean_timing_environment")
def test_execution_deadlines_follow_operator_runtime_and_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import Settings

    monkeypatch.setenv("EXECUTION_MAX_RUNTIME_SECONDS", "120")
    monkeypatch.setenv("EXECUTION_SHUTDOWN_GRACE_SECONDS", "10")
    monkeypatch.setenv("ORPHANED_RUNNING_GRACE_SECONDS", "45")
    # Derived deadlines must ignore independently configured legacy values.
    monkeypatch.setenv("EXECUTION_LEASE_SECONDS", "3600")
    monkeypatch.setenv("EXECUTION_DISPATCH_DEADLINE_SECONDS", "600")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.execution_dispatch_deadline_seconds == 130
    assert settings.execution_lease_seconds == 130
    assert settings.orphaned_running_grace_seconds == 45


@pytest.mark.usefixtures("clean_timing_environment")
@pytest.mark.parametrize("hours", [0, 24, 168])
def test_operator_can_set_approval_wait_timeout(
    monkeypatch: pytest.MonkeyPatch, hours: int
) -> None:
    from src.config import Settings

    monkeypatch.setenv("APPROVAL_WAIT_TIMEOUT_HOURS", str(hours))
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.approval_wait_timeout_hours == hours


@pytest.mark.usefixtures("clean_timing_environment")
def test_negative_approval_wait_timeout_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from src.config import Settings

    monkeypatch.setenv("APPROVAL_WAIT_TIMEOUT_HOURS", "-1")
    with pytest.raises(ValidationError, match="approval_wait_timeout_hours"):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.usefixtures("clean_timing_environment")
@pytest.mark.parametrize("seconds", [0, -1])
def test_nonpositive_orphan_grace_is_rejected(
    monkeypatch: pytest.MonkeyPatch, seconds: int
) -> None:
    from pydantic import ValidationError

    from src.config import Settings

    monkeypatch.setenv("ORPHANED_RUNNING_GRACE_SECONDS", str(seconds))
    with pytest.raises(ValidationError, match="orphaned_running_grace_seconds"):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.usefixtures("clean_timing_environment")
def test_env_example_keeps_approval_expiry_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from dotenv import dotenv_values

    from src.config import Settings

    for name, value in dotenv_values(".env.example").items():
        if value is not None:
            monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.approval_wait_timeout_hours == 0
    assert settings.execution_max_runtime_seconds == 1740
    assert settings.execution_shutdown_grace_seconds == 30
    assert settings.orphaned_running_grace_seconds == 120
