"""Tests for POST /internal/claim-and-run (P1-2)."""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext
from starlette.requests import Request

from src.main import create_app


class _FakeTx:
    """Mimics Prisma's `db.tx()` async context manager.

    Real Prisma's transaction object exposes the same query_raw/
    execute_raw surface as the top-level client, scoped to the
    transaction. Rather than inventing a separate mock object (which
    would let a test assert against `tx.query_raw` while the
    implementation coincidentally reads `db.query_raw`, hiding a bug),
    `__aenter__` returns the same `db` mock the test already configures
    — so `db.query_raw`/`db.execute_raw` assignments in a test are what
    the implementation actually observes inside the `async with` block,
    same as real Prisma.
    """

    def __init__(self, db: MagicMock) -> None:
        self._db = db

    async def __aenter__(self) -> MagicMock:
        return self._db

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflowexecution = MagicMock()

    def _make_tx(**_kwargs: object) -> _FakeTx:
        return _FakeTx(db)

    db.tx = MagicMock(side_effect=_make_tx)
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    return TestClient(app), db


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_claim_and_run_attaches_remote_context_only_during_execution(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from src.api import internal
    from src.api.internal import ClaimAndRunRequest

    observed: list[SpanContext] = []

    async def _observe_execution(*_args: object, **_kwargs: object) -> dict[str, str]:
        observed.append(trace.get_current_span().get_span_context())
        if outcome == "error":
            raise RuntimeError("worker failure")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return {"status": "completed"}

    monkeypatch.setattr(internal, "claim_and_run_execution", _observe_execution)

    def _fake_dependency(_request: Request) -> object:
        return object()

    monkeypatch.setattr(internal, "get_checkpointer", _fake_dependency)
    monkeypatch.setattr(internal, "get_event_bus", _fake_dependency)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/claim-and-run",
            "headers": [
                (b"traceparent", b"00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")
            ],
            "client": ("127.0.0.1", 1234),
        }
    )

    async def _invoke_and_check_cleanup() -> None:
        with contextlib.suppress(RuntimeError, asyncio.CancelledError):
            await internal.claim_and_run(
                payload=ClaimAndRunRequest(executionId="exec-1", kind="run"),
                request=request,
                db=MagicMock(),
                _oidc=None,
            )
        assert not trace.get_current_span().get_span_context().is_valid

    asyncio.run(_invoke_and_check_cleanup())

    assert len(observed) == 1
    assert observed[0].is_remote
    assert observed[0].trace_id == 0x0123456789ABCDEF0123456789ABCDEF


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_claim_and_run_uses_clean_context_when_attachment_fails(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from opentelemetry import context as otel_context

    from src.api import internal
    from src.api.internal import ClaimAndRunRequest

    ambient = SpanContext(
        trace_id=0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,
        span_id=0xBBBBBBBBBBBBBBBB,
        is_remote=False,
    )
    ambient_token = otel_context.attach(trace.set_span_in_context(NonRecordingSpan(ambient)))
    observed: list[SpanContext] = []

    async def _observe_execution(*_args: object, **_kwargs: object) -> dict[str, str]:
        observed.append(trace.get_current_span().get_span_context())
        if outcome == "error":
            raise RuntimeError("worker failure")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return {"status": "completed"}

    def _failing_attach(_context: object) -> object:
        raise RuntimeError("context attach unavailable")

    monkeypatch.setattr(internal, "claim_and_run_execution", _observe_execution)
    monkeypatch.setattr(internal.otel_context, "attach", _failing_attach)

    def _fake_dependency(_request: Request) -> object:
        return object()

    monkeypatch.setattr(internal, "get_checkpointer", _fake_dependency)
    monkeypatch.setattr(internal, "get_event_bus", _fake_dependency)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/claim-and-run",
            "headers": [
                (b"traceparent", b"00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")
            ],
            "client": ("127.0.0.1", 1234),
        }
    )

    async def _invoke() -> None:
        with contextlib.suppress(RuntimeError, asyncio.CancelledError):
            await internal.claim_and_run(
                payload=ClaimAndRunRequest(executionId="exec-1", kind="run"),
                request=request,
                db=MagicMock(),
                _oidc=None,
            )

    try:
        asyncio.run(_invoke())
        assert len(observed) == 1
        assert not observed[0].is_valid
        assert trace.get_current_span().get_span_context() == ambient
    finally:
        otel_context.detach(ambient_token)


async def test_claim_and_run_execution_runs_directly_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`claim_and_run_execution` (the core claim+run logic factored out of
    the `claim_and_run` HTTP handler) must be callable with plain
    db/checkpointer/event_bus objects — no FastAPI `Request` involved.

    This is what `src/execution/cloud_tasks.py`'s dev-mode fallback (no
    CLOUD_TASKS_SERVICE_ACCOUNT configured) needs: it dispatches execution
    in-process instead of enqueueing a real Cloud Task, and has no HTTP
    request to hand `claim_and_run` — only whatever `db` its own caller
    already has. If this function can only run behind the HTTP layer, that
    fallback is impossible without inventing a fake `Request`."""
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

    def _make_tx(**_kwargs: object) -> _FakeTx:
        return _FakeTx(db)

    db.tx = MagicMock(side_effect=_make_tx)

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(self: LangGraphExecutor, execution_id: str) -> None:
        ran.append(execution_id)

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    from src.api.internal import claim_and_run_execution

    result = await claim_and_run_execution(
        "exec-1",
        "run",
        db=db,
        checkpointer=MagicMock(),
        event_bus=MagicMock(),
        worker_id="in-process:exec-1",
    )

    assert result == {"status": "completed"}
    assert ran == ["exec-1"]
    db.execute_raw.assert_awaited_once()


def test_claim_and_run_claims_and_dispatches_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")  # dev mode: OIDC check skipped
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(self: LangGraphExecutor, execution_id: str) -> None:
        ran.append(execution_id)

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
    assert ran == ["exec-1"]
    db.execute_raw.assert_awaited_once()


def test_claim_and_run_no_op_when_already_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """SKIP LOCKED means a second (redelivered) request for the same
    execution finds nothing to claim and returns success without re-running
    — this is the actual single-claim guarantee, not Cloud Tasks itself."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[])  # already locked by another worker
    db.execute_raw = AsyncMock()

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "already_claimed"
    db.execute_raw.assert_not_awaited()


def test_claim_and_run_claim_query_filters_on_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: the claim query MUST filter on status so a
    redelivered Cloud Tasks request can never re-claim (and re-run) an
    execution that already reached a terminal status ('completed',
    'failed', 'canceled') — even once its lease has expired (or was
    never set). Without a status predicate, the claim query only checks
    lease_expires_at, which a redelivery after the (default 1h) lease
    window would satisfy for an already-finished row, causing
    `executor.run()`/`.resume()` to be invoked a second time on a
    terminal execution.

    Terminal transitions clear ownership, so the status predicate must
    still exclude those rows when their lease is null.
    """
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    # Simulate what Postgres would actually do: a terminal-status row
    # never matches, regardless of what query_raw is asked to return by
    # a naive caller — the fake here proves the SQL text itself carries
    # the predicate by parsing which statuses the query allows and
    # applying it against a fake 'completed' row for execution_id.
    fake_row_id = "exec-1"
    fake_row_status = "completed"

    async def _fake_query_raw(sql: str, *args: object) -> list[dict[str, str]]:
        assert "status" in sql, "claim query must filter on status"
        assert re.search(r"status\s*=\s*ANY\(\$\d+::text\[\]\)", sql, re.IGNORECASE), (
            "claim query must filter status via a bound `= ANY($N::text[])` "
            "parameter, not a hardcoded literal"
        )
        assert len(args) >= 2, "claim query must bind the allowed statuses as a parameter"
        allowed = set(args[1])  # type: ignore[arg-type]
        # Terminal statuses must never be claimable, whatever else changes.
        assert "completed" not in allowed
        assert "failed" not in allowed
        assert "canceled" not in allowed
        if fake_row_status not in allowed:
            return []
        return [{"id": fake_row_id}]

    db.query_raw = AsyncMock(side_effect=_fake_query_raw)
    db.execute_raw = AsyncMock()

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(self: LangGraphExecutor, execution_id: str) -> None:
        ran.append(execution_id)

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
    # The completed row must NOT have been claimed/run.
    assert resp.json()["status"] == "already_claimed"
    assert ran == []
    db.execute_raw.assert_not_awaited()


def test_claim_and_run_claim_query_includes_queued_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Important #3 regression: the claim query's allowed-status set MUST
    include 'queued', not just 'running'/'waiting_approval' — a freshly
    created row (P1-2: created 'queued', see LangGraphExecutor.
    start_execution) is exactly the row a fresh Cloud Task delivery is
    meant to claim. Without 'queued' in the predicate, every newly
    created execution would sit unclaimable forever. Complements
    test_claim_and_run_claim_query_filters_on_status, which only asserts
    terminal statuses are EXCLUDED and would pass unchanged even if
    'queued' had been left out of the allowed set entirely.
    """
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()

    async def _fake_query_raw(sql: str, *args: object) -> list[dict[str, str]]:
        assert re.search(r"status\s*=\s*ANY\(\$\d+::text\[\]\)", sql, re.IGNORECASE), (
            "claim query must have a bound `status = ANY($N::text[])` predicate"
        )
        assert len(args) >= 2, "claim query must bind the allowed statuses as a parameter"
        allowed = set(args[1])  # type: ignore[arg-type]
        assert "queued" in allowed, "freshly created 'queued' rows must be claimable"
        return [{"id": "exec-1"}]

    db.query_raw = AsyncMock(side_effect=_fake_query_raw)
    db.execute_raw = AsyncMock()

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(self: LangGraphExecutor, execution_id: str) -> None:
        ran.append(execution_id)

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
    assert ran == ["exec-1"]


@pytest.mark.parametrize("kind, expected_status", [("run", "queued"), ("resume", "running")])
def test_claim_eligibility_is_kind_specific(
    monkeypatch: pytest.MonkeyPatch, kind: str, expected_status: str
) -> None:
    """Active paused or expired attempts must never be replayed by delivery."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()

    async def _query(sql: str, *args: object) -> list[dict[str, str]]:
        assert args[1] == [expected_status]
        assert re.search(r"lease_expires_at\s+IS\s+NULL", sql, re.IGNORECASE)
        assert re.search(r"lease_owner\s+IS\s+NULL", sql, re.IGNORECASE)
        assert not re.search(r"lease_expires_at\s*<", sql, re.IGNORECASE)
        return []

    db.query_raw = AsyncMock(side_effect=_query)
    db.execute_raw = AsyncMock()
    response = cast("Any", client).post(
        "/internal/claim-and-run", json={"executionId": "exec-1", "kind": kind}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "already_claimed"}
    db.execute_raw.assert_not_awaited()


def test_claim_and_run_update_sets_status_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Important #3 regression: the claim UPDATE must unconditionally
    write status='running' — without it, a claimed 'queued' row would
    stay 'queued' forever even though claim-and-run believes it owns the
    row (see the P1-2 justification comment on the UPDATE statement in
    src/api/internal.py)."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _fake_run(self: LangGraphExecutor, execution_id: str) -> None:
        return None

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text

    db.execute_raw.assert_awaited_once()
    call = db.execute_raw.call_args
    sql = call.args[0]
    assert re.search(r"status\s*=\s*'running'", sql, re.IGNORECASE), (
        "claim UPDATE must unconditionally set status='running'"
    )


@pytest.mark.parametrize("kind", ["run", "resume"])
def test_claim_stamps_attempt_and_derived_lease_from_one_timestamp(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    monkeypatch.setenv("EXECUTION_MAX_RUNTIME_SECONDS", "900")
    monkeypatch.setenv("EXECUTION_SHUTDOWN_GRACE_SECONDS", "47")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(variables={"_resume_decision": "approved"})
    )
    frozen_now = datetime(2026, 11, 17, 12, tzinfo=UTC)
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = frozen_now
    monkeypatch.setattr("src.api.internal.datetime", clock)
    monkeypatch.setattr("src.api.internal.LangGraphExecutor.run", AsyncMock())
    monkeypatch.setattr("src.api.internal.LangGraphExecutor.resume", AsyncMock())

    response = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": kind})

    assert response.status_code == 200, response.text
    sql, owner, lease_expires, running_since, execution_id = db.execute_raw.call_args.args
    assert "lease_owner = $1" in sql
    assert "lease_expires_at = $2::timestamptz" in sql
    assert "running_since = $3::timestamptz" in sql
    assert "WHERE id = $4" in sql
    assert owner
    assert running_since == frozen_now
    assert lease_expires == datetime(2026, 11, 17, 12, 15, 47, tzinfo=UTC)
    assert execution_id == "exec-1"
    clock.now.assert_called_once_with(UTC)


def test_claim_and_run_resume_dispatches_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1-2 Task 13: the resume decision travels via the claimed row's
    `variables._resume_decision` (stamped by the enqueueing call site
    BEFORE the Cloud Task fires), not via any request-body field —
    enqueue_execution's Cloud Task body is hardcoded to
    {"executionId": ..., "kind": ...} with no room for it."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(variables={"_resume_decision": "approved"})
    )

    from src.engine.langgraph_executor import LangGraphExecutor

    resumed: list[tuple[str, str]] = []

    async def _fake_resume(self: LangGraphExecutor, execution_id: str, decision: str) -> None:
        resumed.append((execution_id, decision))

    monkeypatch.setattr(LangGraphExecutor, "resume", _fake_resume)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "resume"},
    )
    assert resp.status_code == 200, resp.text
    assert resumed == [("exec-1", "approved")]
    db.workflowexecution.find_unique.assert_awaited_once_with(where={"id": "exec-1"})


def test_claim_and_run_resume_ignores_legacy_decision_field_in_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ClaimAndRunRequest` dropped the `decision` field (P1-2 Task 13) --
    a stray `decision` key in the request body (e.g. a stale caller) must
    be ignored, not consulted; only `variables._resume_decision` on the
    claimed row is authoritative."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(variables={"_resume_decision": "rejected"})
    )

    from src.engine.langgraph_executor import LangGraphExecutor

    resumed: list[tuple[str, str]] = []

    async def _fake_resume(self: LangGraphExecutor, execution_id: str, decision: str) -> None:
        resumed.append((execution_id, decision))

    monkeypatch.setattr(LangGraphExecutor, "resume", _fake_resume)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "resume", "decision": "approved"},
    )
    assert resp.status_code == 200, resp.text
    assert resumed == [("exec-1", "rejected")]


def test_claim_and_run_resume_without_decision_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `_resume_decision` in the claimed row's variables (e.g. a stale
    or malformed enqueue) is a 422, matching the pre-Task-13 "decision is
    required" error shape."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=SimpleNamespace(variables={}))

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "resume"})
    assert resp.status_code == 422


def test_claim_and_run_rejects_missing_oidc_token_when_service_account_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once CLOUD_TASKS_SERVICE_ACCOUNT is configured (i.e. in production),
    a request with no Authorization header must be rejected — this is the
    actual security boundary the endpoint depends on."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    client, _db = _client_with_mock_db()

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 401


def test_claim_and_run_rejects_invalid_oidc_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    client, _db = _client_with_mock_db()

    from google.auth.exceptions import GoogleAuthError

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise GoogleAuthError("bad token")

    monkeypatch.setattr("src.api.internal.id_token.verify_oauth2_token", _raise)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "run"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_claim_and_run_rejects_oidc_token_with_wrong_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validly-signed Google OIDC token is not sufficient — it must be
    signed as the SPECIFIC service account this app's Cloud Tasks queue
    uses, not merely any Google-issued token."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    client, _db = _client_with_mock_db()

    def _verify(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"email": "someone-else@test-project.iam.gserviceaccount.com"}

    monkeypatch.setattr("src.api.internal.id_token.verify_oauth2_token", _verify)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "run"},
        headers={"Authorization": "Bearer some-token"},
    )
    assert resp.status_code == 401


def test_claim_and_run_crash_in_run_marks_row_failed_and_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 Task 13 follow-up: a genuine crash inside `executor.run()` must
    be caught synchronously, the row marked 'failed' with an informative
    message in the DB `error` field (mirroring the deleted
    `_run_with_persistence` wrapper's `f"{type(exc).__name__}: {exc}"`
    format), guarded on `status='running'` (Issue 1) so the write can't
    clobber a concurrent transition, and the HTTP response must carry a
    generic client-facing message (Issue 2) — the raw exception text is
    deliberately NOT included in the response body, only in the DB `error`
    field and the application logs."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()
    db.workflowexecution.update_many = AsyncMock(return_value=1)

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_run(self: LangGraphExecutor, execution_id: str) -> None:
        raise RuntimeError("simulated executor crash")

    monkeypatch.setattr(LangGraphExecutor, "run", _crashing_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})

    assert resp.status_code == 500
    # The raw exception text must NOT appear in the client-facing response
    # body — that's a different retention/IAM surface (Cloud Tasks) than
    # this app's own structured logs and DB.
    assert "RuntimeError" not in resp.text
    assert "simulated executor crash" not in resp.text
    assert "exec-1" in resp.text
    assert "see application logs" in resp.text

    db.workflowexecution.update_many.assert_awaited_once()
    call = db.workflowexecution.update_many.call_args
    assert call.kwargs["where"] == {"id": "exec-1", "status": "running"}
    data = call.kwargs["data"]
    assert data["status"] == "failed"
    assert "RuntimeError" in data["error"]
    assert "simulated executor crash" in data["error"]
    assert data["completedAt"] is not None
    assert data["failureCode"] == "execution_error"
    assert data["runningSince"] is None
    assert data["leaseOwner"] is None
    assert data["leaseExpiresAt"] is None


def test_claim_and_run_crash_in_resume_marks_row_failed_and_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fast-fail guard, resume path: a crash inside `executor.resume()`
    marks the row 'failed' immediately (guarded on status='running') and
    surfaces a generic error response, rather than silently leaving the
    row 'running' for the sweeper to eventually notice, or leaking raw
    exception text into the client-facing response body."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()
    db.workflowexecution.find_unique = AsyncMock(
        return_value=SimpleNamespace(variables={"_resume_decision": "approved"})
    )
    db.workflowexecution.update_many = AsyncMock(return_value=1)

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_resume(self: LangGraphExecutor, execution_id: str, decision: str) -> None:
        raise RuntimeError("simulated resume crash")

    monkeypatch.setattr(LangGraphExecutor, "resume", _crashing_resume)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "resume"},
    )

    assert resp.status_code == 500
    assert "RuntimeError" not in resp.text
    assert "simulated resume crash" not in resp.text
    assert "exec-1" in resp.text
    assert "see application logs" in resp.text

    db.workflowexecution.update_many.assert_awaited_once()
    call = db.workflowexecution.update_many.call_args
    assert call.kwargs["where"] == {"id": "exec-1", "status": "running"}
    data = call.kwargs["data"]
    assert data["status"] == "failed"
    assert "RuntimeError" in data["error"]
    assert "simulated resume crash" in data["error"]
    assert data["completedAt"] is not None
    assert data["failureCode"] == "execution_error"
    assert data["runningSince"] is None
    assert data["leaseOwner"] is None
    assert data["leaseExpiresAt"] is None


def test_claim_and_run_crash_does_not_clobber_concurrent_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 Task 13 follow-up, Issue 1: if a concurrent `cancel_execution`
    already flipped this row to 'canceled' by the time the crash handler's
    guarded `update_many(where={..., "status": "running"})` runs, the guard
    matches zero rows (Prisma's `update_many` returns the affected count).
    The handler must NOT then report the crash as if the write had
    succeeded in overwriting a live row — the row's 'canceled' status is
    already the legitimate, correctly-recorded outcome. Matches
    `create_execution`'s equivalent "lost the race" guard in
    src/api/executions.py."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()
    # Simulate Prisma's update_many finding no matching row: a concurrent
    # cancel already moved status away from 'running'.
    db.workflowexecution.update_many = AsyncMock(return_value=0)

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_run(self: LangGraphExecutor, execution_id: str) -> None:
        raise RuntimeError("simulated executor crash")

    monkeypatch.setattr(LangGraphExecutor, "run", _crashing_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})

    # No 500 here: the row's outcome is already correctly recorded by
    # whatever transition won the race, so this is not an application
    # failure that needs to be reported (and re-reported to Cloud Tasks
    # as a retry-worthy failure) — it's a benign race loss.
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "already_terminal"

    db.workflowexecution.update_many.assert_awaited_once()
    call = db.workflowexecution.update_many.call_args
    assert call.kwargs["where"] == {"id": "exec-1", "status": "running"}


def test_claim_and_run_cancelled_error_is_not_caught_by_fast_fail_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Narrow-scope regression: `asyncio.CancelledError` (graceful worker
    shutdown, e.g. Cloud Run scale-down) must propagate through the new
    fast-fail guard uncaught — it must NOT be treated as a crash and must
    NOT cause this code path to mark the row 'failed'. Cancellation falls
    through to `sweep_expired_leases`'s slower recovery instead, same as a
    hard worker kill.

    Calling the route function directly (bypassing TestClient, which
    would swallow/translate the exception via Starlette's exception
    handling) is what lets this test observe the raw propagation."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    db = MagicMock()
    db.workflowexecution = MagicMock()
    db.workflowexecution.update_many = AsyncMock()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

    def _make_tx(**_kwargs: object) -> _FakeTx:
        return _FakeTx(db)

    db.tx = MagicMock(side_effect=_make_tx)

    from src.api.internal import ClaimAndRunRequest, claim_and_run
    from src.engine.langgraph_executor import LangGraphExecutor

    async def _cancelled_run(self: LangGraphExecutor, execution_id: str) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(LangGraphExecutor, "run", _cancelled_run)

    fake_request = SimpleNamespace(
        client=SimpleNamespace(host="test"),
        app=SimpleNamespace(state=SimpleNamespace(checkpointer=MagicMock(), event_bus=MagicMock())),
    )

    def _fake_get_checkpointer(_req: object) -> MagicMock:
        return MagicMock()

    def _fake_get_event_bus(_req: object) -> MagicMock:
        return MagicMock()

    monkeypatch.setattr("src.api.internal.get_checkpointer", _fake_get_checkpointer)
    monkeypatch.setattr("src.api.internal.get_event_bus", _fake_get_event_bus)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            claim_and_run(
                payload=ClaimAndRunRequest(executionId="exec-1", kind="run"),
                request=fake_request,  # type: ignore[arg-type]
                db=db,
                _oidc=None,
            )
        )

    # The fast-fail guard must not have touched the row at all — this is
    # what proves CancelledError skipped `except Exception` entirely
    # rather than being caught and re-raised after a (wrong) DB write.
    db.workflowexecution.update_many.assert_not_awaited()


def test_claim_and_run_accepts_valid_oidc_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(self: LangGraphExecutor, execution_id: str) -> None:
        ran.append(execution_id)

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    def _verify(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"email": "worker@test-project.iam.gserviceaccount.com"}

    monkeypatch.setattr("src.api.internal.id_token.verify_oauth2_token", _verify)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "run"},
        headers={"Authorization": "Bearer some-valid-token"},
    )
    assert resp.status_code == 200, resp.text
    assert ran == ["exec-1"]
