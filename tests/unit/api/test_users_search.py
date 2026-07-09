"""Tests for GET /users/search (Account + Workflow Sharing plan, Part B)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.users import router as users_router
from src.security.jwt import create_access_token
from src.security.rate_limit import RateLimiter


def _user_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "u2",
        "email": "bob@example.com",
        "displayName": "Bob",
        "isActive": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace]) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(users_router)
    db = MagicMock()
    db.user = MagicMock()
    db.user.find_many = AsyncMock(return_value=rows)
    app.state.db = db
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def test_search_users_returns_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_user_row()])
    token = create_access_token("u1")
    resp = client.get("/users/search?q=bob", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["email"] == "bob@example.com"
    assert "isActive" not in body[0]


def test_search_users_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [])
    resp = client.get("/users/search?q=bob")
    assert resp.status_code == 401


def test_search_users_requires_min_query_length(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [])
    token = create_access_token("u1")
    resp = client.get("/users/search?q=b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


async def test_search_users_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-caller bucket for this route is pre-exhausted -> 429, keyed on user id."""
    client = _client(monkeypatch, [_user_row()])
    token = create_access_token("u1")

    from src.config import get_settings
    from src.security.rate_limit import per_minute_config

    limiter: RateLimiter = client.app.state.rate_limiter  # type: ignore[attr-defined]
    config = per_minute_config(get_settings().rate_limit_users_search_per_minute)
    # Drain the bucket for this route+user key before the real request lands.
    for _ in range(config.capacity):
        await limiter.check("users_search", "u1", config)

    resp = client.get("/users/search?q=bob", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
