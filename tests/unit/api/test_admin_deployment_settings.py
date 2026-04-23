"""Unit tests for admin deployment settings endpoints (Phase 10d)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.security.auth import ensure_admin
from src.security.rate_limit import RateLimiter


def _client_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.deploymentsetting = MagicMock()
    db.user = MagicMock()
    db.llmapikey = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()

    app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]

    return TestClient(app), db


def _client_member(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    db = MagicMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)  # dev-mode 'dev' → member
    db.llmapikey = MagicMock()
    db.deploymentsetting = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    # No dependency_overrides → ensure_admin returns 403
    return TestClient(app), db


def _setting_row(key: str = "tool.tavily.enabled", value: str = "true") -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        value=value,
        updatedAt="2026-04-22T00:00:00Z",
    )


def test_list_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /admin/deployment-settings returns all settings."""
    client, db = _client_admin(monkeypatch)
    db.deploymentsetting.find_many = AsyncMock(
        return_value=[
            _setting_row("tool.tavily.enabled", "true"),
            _setting_row("tool.serper.enabled", "false"),
        ]
    )
    resp = client.get("/admin/deployment-settings")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    keys = {s["key"] for s in body}
    assert "tool.tavily.enabled" in keys
    assert "tool.serper.enabled" in keys


def test_get_missing_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET on an unknown key returns 404."""
    client, db = _client_admin(monkeypatch)
    db.deploymentsetting.find_unique = AsyncMock(return_value=None)
    resp = client.get("/admin/deployment-settings/unknown.key")
    assert resp.status_code == 404


def test_put_creates_if_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """PUT calls create when the key does not exist yet."""
    client, db = _client_admin(monkeypatch)
    db.deploymentsetting.find_unique = AsyncMock(return_value=None)
    db.deploymentsetting.create = AsyncMock(
        return_value=_setting_row("tool.tavily.enabled", "true")
    )
    resp = client.put("/admin/deployment-settings/tool.tavily.enabled", json={"value": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "tool.tavily.enabled"
    assert body["value"] == "true"
    db.deploymentsetting.create.assert_awaited_once()


def test_put_updates_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """PUT calls update when the key already exists."""
    client, db = _client_admin(monkeypatch)
    db.deploymentsetting.find_unique = AsyncMock(
        return_value=_setting_row("tool.tavily.enabled", "true")
    )
    db.deploymentsetting.update = AsyncMock(
        return_value=_setting_row("tool.tavily.enabled", "false")
    )
    resp = client.put("/admin/deployment-settings/tool.tavily.enabled", json={"value": "false"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["value"] == "false"
    db.deploymentsetting.update.assert_awaited_once()


def test_member_cannot_put_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin member receives 403 on PUT."""
    client, _ = _client_member(monkeypatch)
    resp = client.put("/admin/deployment-settings/tool.tavily.enabled", json={"value": "true"})
    assert resp.status_code == 403
