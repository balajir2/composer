"""Unit tests for POST /api/run/{slug} (Phase 10a)."""

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.models import UserRole
from src.db.repos import api_keys as keys_repo
from src.db.repos import users as users_repo
from src.db.repos import workflows as workflows_repo
from src.db.repos.api_keys import ApiKeyRecord
from src.db.repos.workflows import WorkflowRecord
from src.engine.events import ExecutionEventBus
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import (
    FakeSessionmaker,
    RecordingSessionmaker,
    assert_no_network_inside_session,
    make_user,
    make_workflow,
)


def _wf(**overrides: Any) -> WorkflowRecord:
    base: dict[str, Any] = {
        "id": "wf1",
        "user_id": "owner-u",
        "name": "External WF",
        "is_public": False,
        "is_production": True,
        "external_slug": "my-wf",
    }
    base.update(overrides)
    return make_workflow(**base)


def _key(**overrides: Any) -> ApiKeyRecord:
    base: dict[str, Any] = {
        "id": "k1",
        "user_id": "owner-u",
        "label": "caller-key",
        "key_hash": "$2b$12$placeholder",
        "key_prefix": "ck_abc12345",
        "created_at": datetime(2026, 4, 1, tzinfo=UTC),
        "revoked_at": None,
        "expires_at": None,
        "last_used_at": None,
    }
    base.update(overrides)
    return ApiKeyRecord(**base)


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

    def _fake_verify_api_key(key: str, hashed: str) -> bool:
        return True

    monkeypatch.setattr(_aka, "verify_api_key", _fake_verify_api_key)

    app = create_app()
    monkeypatch.setattr(
        keys_repo,
        "first_by_prefix",
        AsyncMock(return_value=key_row if key_row is not None else _key()),
    )
    monkeypatch.setattr(keys_repo, "touch_last_used", AsyncMock())
    monkeypatch.setattr(
        users_repo,
        "get_by_id",
        AsyncMock(
            return_value=make_user(
                id="owner-u",
                role=UserRole(key_owner_role),
                is_active=key_owner_active,
            )
        ),
    )
    app.state.db_sessionmaker = FakeSessionmaker()
    monkeypatch.setattr(workflows_repo, "get_by_slug", AsyncMock(return_value=wf))
    db = MagicMock()
    db.workflowexecution = MagicMock()
    looked_up = existing_execution if existing_execution is not None else start_result
    db.workflowexecution.find_first = AsyncMock(return_value=looked_up)
    db.workflowexecution.find_unique = AsyncMock(return_value=looked_up)
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
    wf = _wf(is_public=True)
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


def test_idempotency_lookup_is_scoped_to_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keys are unique per (workflow, user): the lookup carries the caller's id, so a key
    another user used is invisible and this caller starts their own execution."""
    wf = _wf(is_public=True)
    start_calls: list[dict[str, Any]] = []
    client = _build_client(monkeypatch, wf, start_calls=start_calls)
    find_first = client.app.state.db.workflowexecution.find_first  # type: ignore[attr-defined]
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "idempotencyKey": "shared-key"},
    )
    assert resp.status_code == 200
    assert start_calls == [{"workflow_id": "wf1", "idempotency_key": "shared-key"}]
    find_first.assert_awaited_once_with(
        where={"workflowId": "wf1", "userId": "owner-u", "idempotencyKey": "shared-key"}
    )


def test_foreign_row_from_the_lookup_is_still_refused_with_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backstop: the lookup is per-user so this should be unreachable, but a foreign row
    is never replayed."""
    wf = _wf(is_public=True)
    other = _exec(
        id="other-ex-id", userId="someone-else", status="completed", output="SECRET-OUTPUT"
    )
    start_calls: list[dict[str, Any]] = []
    client = _build_client(monkeypatch, wf, existing_execution=other, start_calls=start_calls)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "idempotencyKey": "shared-key", "sync": True},
    )
    assert resp.status_code == 409
    for leaked in ("other-ex-id", "SECRET-OUTPUT"):
        assert leaked not in resp.text
    assert start_calls == []


def test_idempotency_key_race_lost_relooks_up_scoped_to_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start_execution loses the unique-key race against the caller's own concurrent
    request; the re-lookup includes the caller's id and replays without enqueueing."""
    from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]

    from src.api import run as _run_module

    wf = _wf(is_public=True)
    client = _build_client(monkeypatch, wf)
    mine = _exec(id="exec1", userId="owner-u", status="queued")
    find_first = AsyncMock(side_effect=[None, mine])
    client.app.state.db.workflowexecution.find_first = find_first  # type: ignore[attr-defined]

    async def _lose_race(self: Any, **kwargs: Any) -> Any:
        raise UniqueViolationError({"user_facing_error": {"meta": {}}})  # type: ignore[arg-type]

    monkeypatch.setattr(_run_module.LangGraphExecutor, "start_execution", _lose_race)
    enqueue = AsyncMock()
    monkeypatch.setattr(_run_module, "enqueue_execution", enqueue)

    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "idempotencyKey": "raced-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["executionId"] == "exec1"
    wheres = [c.kwargs["where"] for c in find_first.await_args_list]
    assert wheres == [{"workflowId": "wf1", "userId": "owner-u", "idempotencyKey": "raced-key"}] * 2
    enqueue.assert_not_awaited()


def _lose_race_setup(monkeypatch: pytest.MonkeyPatch, lookups: list[Any]) -> tuple[Any, AsyncMock]:
    from prisma.errors import UniqueViolationError  # pyright: ignore[reportMissingImports]

    from src.api import run as _run_module

    client = _build_client(monkeypatch, _wf(is_public=True))
    client.app.state.db.workflowexecution.find_first = AsyncMock(side_effect=lookups)  # type: ignore[attr-defined]

    async def _lose_race(self: Any, **kwargs: Any) -> Any:
        raise UniqueViolationError({"user_facing_error": {"meta": {}}})  # type: ignore[arg-type]

    monkeypatch.setattr(_run_module.LangGraphExecutor, "start_execution", _lose_race)
    enqueue = AsyncMock()
    monkeypatch.setattr(_run_module, "enqueue_execution", enqueue)
    return client, enqueue


def test_race_lost_to_a_foreign_row_is_409_and_not_enqueued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = _exec(id="other-ex-id", userId="someone-else", status="completed")
    client, enqueue = _lose_race_setup(monkeypatch, [None, foreign])
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "idempotencyKey": "k"},
    )
    assert resp.status_code == 409
    assert "other-ex-id" not in resp.text
    enqueue.assert_not_awaited()


def test_unique_violation_with_no_row_for_the_caller_is_409_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only possible while the DB still has the old per-workflow index."""
    client, enqueue = _lose_race_setup(monkeypatch, [None, None])
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {"x": 1}, "idempotencyKey": "k"},
    )
    assert resp.status_code == 409
    assert "idempotency key already used for this workflow" in resp.text
    enqueue.assert_not_awaited()


def test_idempotency_key_starts_new_execution_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf = _wf(is_public=True)
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
    wf = _wf(is_public=True)
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
    wf = _wf(is_public=True)
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
    wf = _wf(is_public=True)
    client = _build_client(monkeypatch, wf)

    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
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
    wf = _wf(is_public=True)
    client = _build_client(monkeypatch, wf)
    resp = client.post("/api/run/my-wf", json={"input": {}})
    assert resp.status_code == 401


def test_non_production_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(is_production=False)
    client = _build_client(monkeypatch, wf)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 404


def test_private_non_owner_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(is_public=False, user_id="owner-u")
    # Key belongs to a different user:
    key = _key(user_id="someone-else")
    client = _build_client(monkeypatch, wf, key_row=key)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 404


def test_private_admin_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(is_public=False, user_id="owner-u")
    key = _key(user_id="admin-u")
    client = _build_client(monkeypatch, wf, key_row=key, key_owner_role="admin")
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 200


def test_revoked_key_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(is_public=True)
    key = _key(revoked_at=datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC))
    client = _build_client(monkeypatch, wf, key_row=key)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 401


def test_inactive_key_owner_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(is_public=True)
    client = _build_client(monkeypatch, wf, key_owner_active=False)
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code == 401


def test_input_over_size_returns_413(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = _wf(is_public=True)
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
    wf = _wf(is_public=True)
    client = _build_client(monkeypatch, wf)
    non_ascii_input = "€" * 300_000
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": non_ascii_input},
    )
    assert resp.status_code != 413


def test_slug_is_looked_up_in_a_short_session_closed_before_the_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import run as _run_module

    events: list[str] = []
    client = _build_client(monkeypatch, _wf(is_public=True))
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]

    async def _by_slug(*_a: object, **_k: object) -> WorkflowRecord:
        events.append("read")
        return _wf(is_public=True)

    async def _enqueue(*_a: object, **_k: object) -> None:
        events.append("net:enqueue")

    monkeypatch.setattr(workflows_repo, "get_by_slug", _by_slug)
    monkeypatch.setattr(_run_module, "enqueue_execution", _enqueue)

    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )

    assert resp.status_code == 200, resp.text
    read_at = events.index("read")
    assert events[read_at - 1 : read_at + 2] == ["session:enter", "read", "session:exit"]
    assert events.index("net:enqueue") > read_at + 1
    assert_no_network_inside_session(events)


def test_sync_mode_starts_enqueues_and_polls_only_after_the_session_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import run as _run_module

    events: list[str] = []
    client = _build_client(monkeypatch, _wf(is_public=True))
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]

    class _RecordingExec:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def start_execution(self, **kw: Any) -> Any:
            events.append("net:start_execution")
            return _exec()

    async def _enqueue(*_a: object, **_k: object) -> None:
        events.append("net:enqueue")

    async def _poll(**_k: object) -> Any:
        events.append("net:poll")
        return _exec(status="completed", output={"ok": True})

    monkeypatch.setattr(_run_module, "LangGraphExecutor", _RecordingExec)
    monkeypatch.setattr(_run_module, "enqueue_execution", _enqueue)
    client.app.state.db.workflowexecution.find_unique = _poll  # type: ignore[attr-defined]

    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}, "sync": True, "timeoutSeconds": 5},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
    nets = [e for e in events if e.startswith("net:")]
    assert nets == ["net:start_execution", "net:enqueue", "net:poll"]
    assert_no_network_inside_session(events)


def test_run_without_a_database_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no sessionmaker the request must never succeed or crash.

    The API-key auth dependency resolves first and needs the database, so the
    outcome is its 401 "authentication unavailable"; the route's own
    ``require_sessionmaker`` (503) is the second guard behind it. Either is fail-closed.
    """
    client = _build_client(monkeypatch, _wf(is_public=True))
    client.app.state.db_sessionmaker = None  # type: ignore[attr-defined]
    resp = client.post(
        "/api/run/my-wf",
        headers={"Authorization": "Bearer ck_abc123456789"},
        json={"input": {}},
    )
    assert resp.status_code in (401, 503), resp.text
    assert resp.json()["detail"] in ("authentication unavailable", "database unavailable")
