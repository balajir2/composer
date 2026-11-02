"""Behavioral tests for src/api/admin_tools.py: authz, listing and the
test-connection endpoint (timeouts, provider exceptions, DB/runtime key drift)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from src.api import admin_tools as mod
from src.main import create_app
from src.security.auth import ensure_admin
from src.tools.base import ApiKeyAuth, HealthStatus, NoAuth
from src.tools.registry import UnknownProviderError

if TYPE_CHECKING:
    import pytest


class _FakeProvider:
    """Stand-in tool provider with a scripted health_check."""

    def __init__(
        self,
        name: str = "fake",
        auth: Any = None,
        health: HealthStatus | Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.name = name
        self.description = f"{name} tool"
        self.category = "standard"
        self.auth = auth if auth is not None else NoAuth()
        self._health = health or HealthStatus(ok=True, message="fine")
        self._hang = hang

    async def health_check(self) -> HealthStatus:
        if self._hang:
            await asyncio.sleep(3600)
        if isinstance(self._health, Exception):
            raise self._health
        return self._health


def _build(
    monkeypatch: pytest.MonkeyPatch, *, admin: bool, environment: str = "development"
) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", environment)
    from src.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    db = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    db.llmapikey.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    if admin:
        app.dependency_overrides[ensure_admin] = lambda: "admin-1"
    return TestClient(app), db


def _install(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider) -> None:
    def _get(name: str) -> _FakeProvider:
        if name != provider.name:
            raise UnknownProviderError(f"No provider registered for {name!r}")
        return provider

    monkeypatch.setattr(mod, "get_provider", _get)


# ─── authorization ──────────────────────────────────────────────────────────


def test_list_tools_forbidden_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build(monkeypatch, admin=False)
    assert client.get("/admin/tools").status_code == 403


def test_test_connection_forbidden_for_non_admin_and_never_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    provider.health_check = AsyncMock(side_effect=AssertionError("must not run"))  # type: ignore[method-assign]
    _install(monkeypatch, provider)
    client, _ = _build(monkeypatch, admin=False)
    assert client.post("/admin/tools/fake/test-connection").status_code == 403


def test_admin_tools_reject_unauthenticated_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build(monkeypatch, admin=False, environment="production")
    assert client.get("/admin/tools").status_code == 401
    assert client.post("/admin/tools/fake/test-connection").status_code == 401


# ─── listing ────────────────────────────────────────────────────────────────


def test_list_tools_returns_standard_providers_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _list(*, category: str | None = None) -> list[_FakeProvider]:
        seen["category"] = category
        return [_FakeProvider("alpha"), _FakeProvider("beta")]

    monkeypatch.setattr(mod, "list_providers", _list)
    client, _ = _build(monkeypatch, admin=True)
    resp = client.get("/admin/tools")
    assert resp.status_code == 200
    assert [t["name"] for t in resp.json()] == ["alpha", "beta"]
    assert seen["category"] == "standard"


# ─── test-connection ────────────────────────────────────────────────────────


def test_test_connection_unknown_tool_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeProvider())
    client, _ = _build(monkeypatch, admin=True)
    assert client.post("/admin/tools/ghost/test-connection").status_code == 404


def test_test_connection_reports_healthy_provider_with_no_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeProvider())
    client, _ = _build(monkeypatch, admin=True)
    body = client.post("/admin/tools/fake/test-connection").json()
    assert body == {"ok": True, "message": "fine", "has_db_key": False, "has_runtime_key": True}


def test_test_connection_health_check_exception_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeProvider(health=ConnectionError("upstream refused")))
    client, _ = _build(monkeypatch, admin=True)
    resp = client.post("/admin/tools/fake/test-connection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == "ConnectionError: upstream refused"


def test_test_connection_health_check_timeout_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_HEALTH_CHECK_TIMEOUT_SEC", 0.05)
    _install(monkeypatch, _FakeProvider(hang=True))
    client, _ = _build(monkeypatch, admin=True)
    body = client.post("/admin/tools/fake/test-connection").json()
    assert body["ok"] is False
    assert "exceeded" in body["message"]


def test_test_connection_db_lookup_timeout_degrades_to_no_db_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeProvider())
    client, db = _build(monkeypatch, admin=True)
    db.llmapikey.find_unique = AsyncMock(side_effect=TimeoutError())
    resp = client.post("/admin/tools/fake/test-connection")
    assert resp.status_code == 200
    assert resp.json()["has_db_key"] is False


def test_test_connection_flags_db_key_not_loaded_into_runtime_when_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = ApiKeyAuth(env_var="FAKE_KEY", settings_field="definitely_not_a_setting")
    provider = _FakeProvider(auth=auth, health=HealthStatus(ok=False, message="FAKE_KEY missing"))
    _install(monkeypatch, provider)
    client, db = _build(monkeypatch, admin=True)
    db.llmapikey.find_unique = AsyncMock(return_value=SimpleNamespace(encryptedKey="ciphertext"))
    body = client.post("/admin/tools/fake/test-connection").json()
    assert body["ok"] is False
    assert body["has_db_key"] is True
    assert body["has_runtime_key"] is False
    assert "not yet loaded" in body["message"]
    assert "ciphertext" not in str(body)


def test_test_connection_has_runtime_key_reflects_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-runtime")
    auth = ApiKeyAuth(env_var="TAVILY_API_KEY", settings_field="tavily_api_key")
    _install(monkeypatch, _FakeProvider(auth=auth))
    client, _ = _build(monkeypatch, admin=True)
    body = client.post("/admin/tools/fake/test-connection").json()
    assert body["has_runtime_key"] is True
    assert "tvly-runtime" not in str(body)
