"""Unit tests for POST /api/run/{slug} (Phase 10a)."""

import time
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
    existing_execution: Any | None = None,
    start_calls: list[dict[str, Any]] | None = None,
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
    db.workflowexecution.find_unique = AsyncMock(
        return_value=existing_execution if existing_execution is not None else start_result
    )
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
            self,
            *,
            workflow_id: str,
            input: Any,
            user_id: str | None,
            idempotency_key: str | None = None,
        ) -> Any:
            if start_calls is not None:
                start_calls.append({"workflow_id": workflow_id, "idempotency_key": idempotency_key})
            return _exec(id="exec1", workflowId=workflow_id, userId=user_id or "owner-u")

        async def run(self, execution_id: str) -> None:
            return None

    monkeypatch.setattr(_run_module, "LangGraphExecutor", _FakeExec)

    # POST /api/run/{slug} now enqueues a real Cloud Task (P1-2) instead
    # of a fire-and-forget asyncio.create_task. Default-patch it to a
    # no-op so tests that don't care about enqueueing don't trip over
    # constructing a real `tasks_v2.CloudTasksAsyncClient()` (no ADC
    # credentials in this test environment). Tests that DO care override
    # this themselves via the same `monkeypatch` instance, AFTER calling
    # `_build_client` (monkeypatch.setattr's last call wins).
    monkeypatch.setattr(_run_module, "enqueue_execution", AsyncMock())

    return TestClient(app)


def test_idempotency_key_replays_existing_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller retrying POST /api/run/{slug} with the same idempotencyKey
    (e.g. an HTTP client timeout retry) must get the original execution
    back, not start a second one — otherwise a Jira/email node inside the
    workflow fires twice for one logical call (P1-3)."""
    wf = _wf(isPublic=True)
    existing = _exec(id="prior-exec", status="completed")
    start_calls: list[dict[str, Any]] = []
    client = _build_client(monkeypatch, wf, existing_execution=existing, start_calls=start_calls)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "idempotencyKey": "retry-key-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["executionId"] == "prior-exec"
    assert start_calls == []


def test_idempotency_key_starts_new_execution_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf = _wf(isPublic=True)
    start_calls: list[dict[str, Any]] = []
    client = _build_client(monkeypatch, wf, start_calls=start_calls)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "idempotencyKey": "fresh-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["executionId"] == "exec1"
    assert start_calls == [{"workflow_id": "wf1", "idempotency_key": "fresh-key"}]


def test_sync_mode_returns_immediately_on_waiting_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync=true must treat waiting_approval as a meaningful terminal-ish
    response and return right away — polling until timeoutSeconds and then
    reporting a hard-coded 'running' status when the row is actually
    waiting_approval is misleading to a caller that can't tell whether the
    workflow is still executing or paused for a human decision (P1-6)."""
    wf = _wf(isPublic=True)
    waiting_row = _exec(status="waiting_approval", output=None)
    client = _build_client(monkeypatch, wf, start_result=waiting_row)
    started = time.monotonic()
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "sync": True, "timeoutSeconds": 5},
    )
    elapsed = time.monotonic() - started
    assert resp.status_code == 200
    assert resp.json()["status"] == "waiting_approval"
    assert elapsed < 2, "should return immediately, not poll for the full timeout"


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


def test_async_run_enqueues_cloud_task_instead_of_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 full replacement: POST /api/run/{slug} must enqueue a Cloud
    Task (kind='run') rather than a fire-and-forget asyncio.create_task —
    Cloud Run can scale a request-bound background task's instance to
    zero mid-run, which Cloud Tasks' HTTP-push delivery to
    /internal/claim-and-run is immune to (ADR-0033, same fix as Task 12's
    POST /executions replacement)."""
    wf = _wf(isPublic=True)
    client = _build_client(monkeypatch, wf)

    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.api.run.enqueue_execution", _fake_enqueue)

    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}},
    )
    assert resp.status_code == 200, resp.text
    assert enqueued == [("exec1", "run")]


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


def test_input_non_ascii_measured_by_utf8_bytes_not_escaped_json_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same UTF-8-byte-vs-escaped-char-count bug as POST /executions
    (P1-6): a 300,000-char string of euro signs is 900,002 real UTF-8
    bytes (under the 1 MB cap) but 1,800,002 chars once json.dumps's
    default ensure_ascii=True escapes each '€' to `\\u20ac`."""
    wf = _wf(isPublic=True)
    client = _build_client(monkeypatch, wf)
    non_ascii_input = "€" * 300_000
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": non_ascii_input},
    )
    assert resp.status_code != 413
