"""Tests for GET /users/search (Account + Workflow Sharing plan, Part B)."""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.users import router as users_router
from src.db.repos import users as users_repo
from src.security.jwt import create_access_token
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import FakeSessionmaker, make_user, override_session


def _client(monkeypatch: pytest.MonkeyPatch, rows: list[users_repo.UserRecord]) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "production")
    from src.config import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(users_router)
    override_session(app)
    app.state.db_sessionmaker = (
        FakeSessionmaker()
    )  # production fails closed without a live user check
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="u1")))
    monkeypatch.setattr(users_repo, "search_active", AsyncMock(return_value=rows))
    app.state.rate_limiter = RateLimiter()
    return TestClient(app)


def _user_row() -> users_repo.UserRecord:
    return make_user(id="u2", email="bob@example.com", display_name="Bob")


def test_search_users_returns_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_user_row()])
    token = create_access_token("u1")
    resp = client.get("/users/search?q=bob", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["email"] == "bob@example.com"
    assert body[0]["displayName"] == "Bob"
    args = users_repo.search_active.await_args  # type: ignore[attr-defined]
    assert args.args[1:] == ("bob", 20)
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

    limiter = cast("RateLimiter", client.app.state.rate_limiter)  # type: ignore[attr-defined]
    config = per_minute_config(get_settings().rate_limit_users_search_per_minute)
    # Drain the bucket for this route+user key before the real request lands.
    for _ in range(config.capacity):
        await limiter.check("users_search", "u1", config)

    resp = client.get("/users/search?q=bob", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
