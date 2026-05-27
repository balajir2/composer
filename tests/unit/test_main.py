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


# ── CORS (standalone + production) ──────────────────────────────
#
# Phase 7a only plumbed an allowed origin for embedded mode.  A standalone
# production deploy with no env var ended up with allow_origins=[] (every
# preflight rejected), which silently broke the Cloud Run frontend.
# These tests pin down the new branch behaviour.


def _cors_allow_origins(app: object) -> list[str]:
    """Pull the CORSMiddleware's allow_origins back out of the constructed app."""
    from fastapi.middleware.cors import CORSMiddleware

    user_middleware = getattr(app, "user_middleware", [])
    for mw in user_middleware:
        if getattr(mw, "cls", None) is CORSMiddleware:
            kwargs = getattr(mw, "kwargs", {}) or {}
            origins = kwargs.get("allow_origins", [])
            return list(origins) if origins is not None else []
    raise AssertionError("CORSMiddleware not registered on the app")


def test_cors_standalone_development_allows_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("COMPOSER_FRONTEND_ORIGINS", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    assert _cors_allow_origins(create_app()) == ["*"]


def test_cors_standalone_production_uses_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "COMPOSER_FRONTEND_ORIGINS",
        " https://a.example.com , https://b.example.com ",
    )
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    assert _cors_allow_origins(create_app()) == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_cors_standalone_production_empty_origins_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("COMPOSER_FRONTEND_ORIGINS", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    with caplog.at_level("WARNING", logger="src.main"):
        app = create_app()
    assert _cors_allow_origins(app) == []
    assert any("COMPOSER_FRONTEND_ORIGINS" in r.message for r in caplog.records), (
        "expected a startup warning about empty CORS allowlist"
    )


def test_cors_embedded_uses_iep_ui_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "embedded")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("IEP_JWT_ISSUER", "https://iep.test")
    monkeypatch.setenv("IEP_SHARED_SECRET", "secret-for-testing-32-chars-minimum")
    monkeypatch.setenv("IEP_UI_ORIGIN", "https://iep.example.com")
    # Even if a standalone-style env var is set, embedded mode must ignore it.
    monkeypatch.setenv("COMPOSER_FRONTEND_ORIGINS", "https://leaked.example.com")
    from src.config import get_settings

    get_settings.cache_clear()
    from src.main import create_app

    assert _cors_allow_origins(create_app()) == ["https://iep.example.com"]
