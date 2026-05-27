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
