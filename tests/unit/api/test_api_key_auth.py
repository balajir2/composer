"""Unit tests for the ck_ API-key dependency (own short session, fails closed)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.db.models import UserRole
from src.db.repos import api_keys as keys_repo
from src.db.repos import users as users_repo
from src.db.repos.api_keys import ApiKeyRecord
from src.security import api_key_auth as aka
from src.security.api_key_auth import ApiKeyAuthResult, get_current_api_key_user
from tests.unit.db_fakes import FakeSession, FakeSessionmaker, make_user

AUTH = {"Authorization": "Bearer ck_abc123456789"}


def _key(**overrides: Any) -> ApiKeyRecord:
    base: dict[str, Any] = {
        "id": "k1",
        "user_id": "u1",
        "label": "l",
        "key_hash": "h",
        "key_prefix": "ck_abc12345",
        "created_at": datetime(2026, 4, 1, tzinfo=UTC),
        "last_used_at": None,
        "expires_at": None,
        "revoked_at": None,
    }
    base.update(overrides)
    return ApiKeyRecord(**base)


class _RecCtx:
    def __init__(self, session: FakeSession, events: list[str]) -> None:
        self._session = session
        self._events = events

    async def __aenter__(self) -> FakeSession:
        self._events.append("enter")
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        self._events.append("exit")
        return False


class _RecordingSessionmaker:
    """Records session enter/exit so tests can order them against the route body."""

    def __init__(self, events: list[str]) -> None:
        self.session = FakeSession()
        self.events = events

    def __call__(self) -> _RecCtx:
        return _RecCtx(self.session, self.events)


def _app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    key: ApiKeyRecord | None = None,
    user_active: bool = True,
    user_missing: bool = False,
    verify: bool = True,
    sessionmaker: Any = None,
    events: list[str] | None = None,
) -> TestClient:
    monkeypatch.setattr(keys_repo, "first_by_prefix", AsyncMock(return_value=key))
    monkeypatch.setattr(keys_repo, "touch_last_used", AsyncMock())
    monkeypatch.setattr(
        users_repo,
        "get_by_id",
        AsyncMock(
            return_value=None
            if user_missing
            else make_user(role=UserRole.ADMIN, is_active=user_active)
        ),
    )

    def _verify(_token: str, _hashed: str) -> bool:
        return verify

    monkeypatch.setattr(aka, "verify_api_key", _verify)
    app = FastAPI()

    @app.get("/probe")
    async def probe(  # pyright: ignore[reportUnusedFunction]
        auth: ApiKeyAuthResult = Depends(get_current_api_key_user),
    ) -> dict[str, str]:
        if events is not None:
            events.append("route")
        return {"user": auth.user_id, "role": auth.role, "key": auth.api_key_id}

    if sessionmaker is not None:
        app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


def test_success_returns_role_value_and_touches_last_used(monkeypatch: pytest.MonkeyPatch) -> None:
    sm = FakeSessionmaker()
    client = _app(monkeypatch, key=_key(), sessionmaker=sm)
    resp = client.get("/probe", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"user": "u1", "role": "admin", "key": "k1"}
    keys_repo.touch_last_used.assert_awaited_once()  # type: ignore[attr-defined]
    assert sm.session.commit.await_count == 1


def test_connection_released_before_route_body(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    client = _app(
        monkeypatch, key=_key(), sessionmaker=_RecordingSessionmaker(events), events=events
    )
    assert client.get("/probe", headers=AUTH).status_code == 200
    assert events == ["enter", "exit", "route"]


@pytest.mark.parametrize("environment", ["development", "production"])
def test_no_sessionmaker_fails_closed(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    client = _app(monkeypatch, key=_key())
    resp = client.get("/probe", headers=AUTH)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "authentication unavailable"


def test_missing_header_and_non_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app(monkeypatch, key=_key(), sessionmaker=FakeSessionmaker())
    r = client.get("/probe")
    assert (r.status_code, r.json()["detail"]) == (401, "missing Authorization header")
    r = client.get("/probe", headers={"Authorization": "Bearer jwt.token"})
    assert (r.status_code, r.json()["detail"]) == (401, "not an API key")


def test_unknown_and_revoked_are_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (None, _key(revoked_at=datetime(2026, 4, 2, tzinfo=UTC))):
        client = _app(monkeypatch, key=key, sessionmaker=FakeSessionmaker())
        r = client.get("/probe", headers=AUTH)
        assert (r.status_code, r.json()["detail"]) == (401, "invalid API key")


def test_expired_checked_before_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = _key(expires_at=datetime.now(UTC) - timedelta(days=1))
    client = _app(monkeypatch, key=expired, verify=False, sessionmaker=FakeSessionmaker())
    r = client.get("/probe", headers=AUTH)
    assert (r.status_code, r.json()["detail"]) == (401, "API key expired")


def test_hash_mismatch_is_invalid_and_does_not_touch(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app(monkeypatch, key=_key(), verify=False, sessionmaker=FakeSessionmaker())
    r = client.get("/probe", headers=AUTH)
    assert (r.status_code, r.json()["detail"]) == (401, "invalid API key")
    keys_repo.touch_last_used.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.parametrize("missing", [True, False])
def test_inactive_or_missing_owner(monkeypatch: pytest.MonkeyPatch, missing: bool) -> None:
    client = _app(
        monkeypatch,
        key=_key(),
        user_missing=missing,
        user_active=False,
        sessionmaker=FakeSessionmaker(),
    )
    r = client.get("/probe", headers=AUTH)
    assert (r.status_code, r.json()["detail"]) == (401, "API key owner is inactive")


def test_touch_failure_rolls_back_and_still_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    sm = FakeSessionmaker()
    client = _app(monkeypatch, key=_key(), sessionmaker=sm)
    monkeypatch.setattr(keys_repo, "touch_last_used", AsyncMock(side_effect=RuntimeError("db")))
    assert client.get("/probe", headers=AUTH).status_code == 200
    assert sm.session.rollback.await_count == 1


@pytest.mark.parametrize("missing", [True, False])
def test_owner_check_still_runs_after_touch_failure(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    client = _app(
        monkeypatch,
        key=_key(),
        user_missing=missing,
        user_active=False,
        sessionmaker=FakeSessionmaker(),
    )
    monkeypatch.setattr(keys_repo, "touch_last_used", AsyncMock(side_effect=RuntimeError("db")))
    r = client.get("/probe", headers=AUTH)
    assert (r.status_code, r.json()["detail"]) == (401, "API key owner is inactive")


def test_touch_commit_failure_still_checks_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    sm = FakeSessionmaker()
    sm.session.commit.side_effect = RuntimeError("commit failed")
    client = _app(monkeypatch, key=_key(), user_active=False, sessionmaker=sm)
    r = client.get("/probe", headers=AUTH)
    assert (r.status_code, r.json()["detail"]) == (401, "API key owner is inactive")
    assert sm.session.rollback.await_count == 1


def test_expiry_boundary_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    t = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(aka, "utcnow", lambda: t)
    ok = _app(monkeypatch, key=_key(expires_at=t), sessionmaker=FakeSessionmaker())
    assert ok.get("/probe", headers=AUTH).status_code == 200
    expired = _app(
        monkeypatch,
        key=_key(expires_at=t - timedelta(milliseconds=1)),
        sessionmaker=FakeSessionmaker(),
    )
    r = expired.get("/probe", headers=AUTH)
    assert (r.status_code, r.json()["detail"]) == (401, "API key expired")
