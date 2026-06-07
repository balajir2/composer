"""Unit tests for POST /api/run/{slug} (Phase 10a)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.engine.events import ExecutionEventBus
from src.main import create_app
from src.security.rate_limit import RateLimiter


def _wf(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "wf1",
        "userId": "owner-u",
        "name": "External WF",
        "nodes": [],
        "edges": [],
        "tags": [],
        "isTemplate": False,
        "isPublic": False,
        "isProduction": True,
        "externalSlug": "my-wf",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _key(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "k1",
        "userId": "owner-u",
        "label": "caller-key",
        "keyHash": "$2b$12$placeholder",
        "keyPrefix": "ck_abc12345",
        "revokedAt": None,
        "expiresAt": None,
        "lastUsedAt": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _exec(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "exec1",
        "workflowId": "wf1",
        "userId": "owner-u",
        "status": "running",
        "currentNodeId": None,
        "nodeResults": {},
        "variables": {},
        "input": None,
        "output": None,
        "error": None,
        "threadId": "t1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    wf: Any,
    key_owner_role: str = "member",
    key_owner_active: bool = True,
    key_row: Any | None = None,
    start_result: Any | None = None,
) -> TestClient:
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from src.config import get_settings

    get_settings.cache_clear()

    # Bypass bcrypt verify — patch at the usage site (api_key_auth already bound the name).
    from src.security import api_key_auth as _aka

    monkeypatch.setattr(_aka, "verify_api_key", lambda key, hashed: True)  # pyright: ignore[reportUnknownLambdaType]

    app = create_app()
    db = MagicMock()
    db.apikey = MagicMock()
    db.apikey.find_first = AsyncMock(return_value=key_row if key_row is not None else _key())
    db.apikey.update = AsyncMock()
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(
            id="owner-u",
            role=SimpleNamespace(value=key_owner_role),
            isActive=key_owner_active,
        )
    )
    db.workflow = MagicMock()
    db.workflow.find_unique = AsyncMock(return_value=wf)
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=start_result)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()

    # Patch LangGraphExecutor at the usage site (src.api.run has already imported the name).
    from src.api import run as _run_module

    class _FakeExec:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def start_execution(
            self, *, workflow_id: str, input: Any, user_id: str | None
        ) -> Any:
            return _exec(id="exec1", workflowId=workflow_id, userId=user_id or "owner-u")

        async def run(self, execution_id: str) -> None:
            return None

    monkeypatch.setattr(_run_module, "LangGraphExecutor", _FakeExec)

    return TestClient(app)


def test_async_run_returns_200_with_stream_url(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(isPublic=True)
    client = _build_client(monkeypatch, wf)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["executionId"] == "exec1"
    assert body["workflowId"] == wf.id
    assert body["streamUrl"].endswith("/executions/exec1/ws")


def test_missing_bearer_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(isPublic=True)
    client = _build_client(monkeypatch, wf)
    resp = client.post("/api/run/my-wf", json={"input": {}})
    assert resp.status_code == 401


def test_non_production_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(isProduction=False)
    client = _build_client(monkeypatch, wf)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 404


def test_private_non_owner_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(isPublic=False, userId="owner-u")
    # Key belongs to a different user:
    key = _key(userId="someone-else")
    client = _build_client(monkeypatch, wf, key_row=key)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 404


def test_private_admin_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(isPublic=False, userId="owner-u")
    key = _key(userId="admin-u")
    client = _build_client(monkeypatch, wf, key_row=key, key_owner_role="admin")
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 200


def test_revoked_key_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime

    wf = _wf(isPublic=True)
    key = _key(revokedAt=datetime(2026, 4, 1, 0, 0, 0))
    client = _build_client(monkeypatch, wf, key_row=key)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 401


def test_inactive_key_owner_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(isPublic=True)
    client = _build_client(monkeypatch, wf, key_owner_active=False)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 401


def test_input_over_size_returns_413(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(isPublic=True)
    client = _build_client(monkeypatch, wf)
    huge = "x" * 1_500_000
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": huge},
    )
    assert resp.status_code == 413
