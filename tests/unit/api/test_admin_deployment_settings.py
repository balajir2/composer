"""Unit tests for admin deployment settings endpoints (Phase 10d)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import deployment_settings as settings_repo
from src.db.repos.deployment_settings import SettingRecord
from src.main import create_app
from src.security.auth import ensure_admin
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSession, override_session


def _client_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeSession]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    session = override_session(app)

    app.dependency_overrides[ensure_admin] = lambda: "dev"  # type: ignore[attr-defined]

    return TestClient(app, raise_server_exceptions=False), session


def _client_member(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    override_session(app)  # route resolves its session before the admin gate runs
    # ensure_admin is deliberately not overridden
    return TestClient(app)


def _record(key: str = "tool.tavily.enabled", value: str = "true") -> SettingRecord:
    return SettingRecord(key=key, value=value, updated_at=datetime(2026, 4, 22, tzinfo=UTC))


def test_list_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /admin/deployment-settings returns all settings."""
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(
        settings_repo,
        "list_all",
        AsyncMock(
            return_value=[
                _record("tool.tavily.enabled", "true"),
                _record("tool.serper.enabled", "false"),
            ]
        ),
    )
    resp = client.get("/admin/deployment-settings")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert session.commit.await_count == 0
    keys = {s["key"] for s in body}
    assert "tool.tavily.enabled" in keys
    assert "tool.serper.enabled" in keys


def test_get_missing_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET on an unknown key returns 404."""
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(settings_repo, "get", AsyncMock(return_value=None))
    resp = client.get("/admin/deployment-settings/unknown.key")
    assert resp.status_code == 404
    assert session.commit.await_count == 0


def test_get_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET on a known key returns its value."""
    client, _ = _client_admin(monkeypatch)
    monkeypatch.setattr(settings_repo, "get", AsyncMock(return_value=_record()))
    resp = client.get("/admin/deployment-settings/tool.tavily.enabled")
    assert resp.status_code == 200
    assert resp.json() == {"key": "tool.tavily.enabled", "value": "true"}


def test_put_upserts_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    """PUT upserts through the repository and commits once."""
    client, session = _client_admin(monkeypatch)
    upsert = AsyncMock(return_value=_record("tool.tavily.enabled", "true"))
    monkeypatch.setattr(settings_repo, "upsert", upsert)
    resp = client.put("/admin/deployment-settings/tool.tavily.enabled", json={"value": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "tool.tavily.enabled"
    assert body["value"] == "true"
    upsert.assert_awaited_once()
    assert upsert.await_args is not None
    assert upsert.await_args.args[1:] == ("tool.tavily.enabled", "true")
    assert session.commit.await_count == 1


def test_put_upsert_failure_is_500(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(settings_repo, "upsert", AsyncMock(side_effect=RuntimeError("boom")))
    resp = client.put("/admin/deployment-settings/k", json={"value": "v"})
    assert resp.status_code == 500
    assert session.commit.await_count == 0


def test_put_commit_failure_is_500(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _client_admin(monkeypatch)
    monkeypatch.setattr(settings_repo, "upsert", AsyncMock(return_value=_record("k", "v")))
    session.commit.side_effect = RuntimeError("commit failed")
    resp = client.put("/admin/deployment-settings/k", json={"value": "v"})
    assert resp.status_code == 500


def test_member_cannot_put_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin member receives 403 on PUT."""
    client = _client_member(monkeypatch)
    resp = client.put("/admin/deployment-settings/tool.tavily.enabled", json={"value": "true"})
    assert resp.status_code == 403
