"""Tests for create_app()'s mode-aware behavior (Phase 7a)."""

import pytest


def test_standalone_includes_auth_register_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/auth/register" in paths
    assert "/auth/login" in paths
    assert "/auth/refresh" in paths
    assert "/auth/disconnect" in paths
    assert "/auth/me" in paths


def test_embedded_excludes_auth_register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret-for-testing-32-chars-minimum")
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    # Standalone-only routes absent
    assert "/auth/register" not in paths
    assert "/auth/login" not in paths
    assert "/auth/refresh" not in paths
    assert "/auth/disconnect" not in paths
    # Common route stays
    assert "/auth/me" in paths


def test_embedded_missing_issuer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret-for-testing-32-chars-minimum")
    monkeypatch.delenv("IEP_JWT_ISSUER", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    with pytest.raises(RuntimeError, match="IEP_JWT_ISSUER"):
        create_app()


def test_embedded_missing_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.delenv("IEP_SHARED_SECRET", raising=False)
    monkeypatch.delenv("IEP_JWKS_URL", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    with pytest.raises(RuntimeError, match="IEP_SHARED_SECRET"):
        create_app()


def test_standalone_does_not_raise_on_missing_iep_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone mode must not require any IEP_* vars."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    for var in ["IEP_JWT_ISSUER", "IEP_SHARED_SECRET", "IEP_JWKS_URL", "IEP_UI_ORIGIN"]:
        monkeypatch.delenv(var, raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    app = create_app()  # should not raise
    assert app is not None
