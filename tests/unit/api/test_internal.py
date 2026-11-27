"""Tests for POST /internal/claim-and-run (P1-2)."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext
from starlette.requests import Request

from src.db.repos.executions import ClaimedExecution
from src.engine.events_pg import PostgresEventStore
from src.main import create_app
from tests.unit.db_fakes import FakeSessionmaker
from tests.unit.execution_fakes import FakeExecutions


def _client_with_fakes(
    monkeypatch: pytest.MonkeyPatch, **fake_kwargs: Any
) -> tuple[TestClient, FakeExecutions]:
    """An app whose sessionmaker is a fake and whose executions repo is patched."""
    app = create_app()
    fake = FakeExecutions(monkeypatch, **fake_kwargs)
    app.state.db_sessionmaker = fake.sessionmaker
    app.state.checkpointer = MagicMock()
    app.state.event_bus = PostgresEventStore(fake.sessionmaker)
    return TestClient(app), fake


def _claimed(variables: Any = None) -> ClaimedExecution:
    return ClaimedExecution(
        id="exec-1",
        variables=variables if variables is not None else {},
        attempt_id="attempt-1",
        attempt_ordinal=1,
    )


def _scope_request(headers: list[tuple[bytes, bytes]]) -> Request:
    """A bare request whose app carries a fake sessionmaker (no TestClient, no lifespan)."""
    app = SimpleNamespace(state=SimpleNamespace(db_sessionmaker=FakeSessionmaker()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/claim-and-run",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "app": app,
        }
    )


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
    request = _scope_request(
        [(b"traceparent", b"00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")]
    )

    async def _invoke_and_check_cleanup() -> None:
        with contextlib.suppress(RuntimeError, asyncio.CancelledError):
            await internal.claim_and_run(
                payload=ClaimAndRunRequest(executionId="exec-1", kind="run"),
                request=request,
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
    request = _scope_request(
        [(b"traceparent", b"00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")]
    )

    async def _invoke() -> None:
        with contextlib.suppress(RuntimeError, asyncio.CancelledError):
            await internal.claim_and_run(
                payload=ClaimAndRunRequest(executionId="exec-1", kind="run"),
                request=request,
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
    the `claim_and_run` HTTP handler) must be callable with a plain
    sessionmaker/checkpointer/event_bus — no FastAPI `Request` involved.

    This is what `src/execution/cloud_tasks.py`'s dev-mode fallback (no
    CLOUD_TASKS_SERVICE_ACCOUNT configured) needs: it dispatches execution
    in-process instead of enqueueing a real Cloud Task, and has no HTTP
    request to hand `claim_and_run`. If this function can only run behind the
    HTTP layer, that fallback is impossible without inventing a fake
    `Request`."""
    fake = FakeExecutions(monkeypatch, claim_result=_claimed())

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[tuple[str, str | None]] = []

    async def _fake_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
        from src.telemetry.context import current_correlation

        correlation = current_correlation()
        assert correlation.execution_id == execution_id
        assert correlation.attempt_id == attempt_id
        ran.append((execution_id, attempt_id))

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    from src.api.internal import claim_and_run_execution

    result = await claim_and_run_execution(
        "exec-1",
        "run",
        sessionmaker=fake.sessionmaker,
        checkpointer=MagicMock(),
        event_bus=MagicMock(),
        worker_id="in-process:exec-1",
    )

    assert result == {"status": "completed"}
    assert ran == [("exec-1", "attempt-1")]
    assert len(fake.claim_calls) == 1


async def test_claim_session_is_committed_and_closed_before_the_run_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering: claim -> commit -> session closed -> THEN the executor runs.

    The claim's SET LOCAL statement_timeout lasts until its transaction ends and the lease
    must be durable before any graph or external call, so nothing may run inside the claim's
    session."""
    events: list[str] = []
    fake = FakeExecutions(monkeypatch, claim_result=_claimed(), events=events)

    from src.api.internal import claim_and_run_execution
    from src.engine.langgraph_executor import LangGraphExecutor

    async def _fake_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
        events.append("net:run")

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    await claim_and_run_execution(
        "exec-1",
        "run",
        sessionmaker=fake.sessionmaker,
        checkpointer=MagicMock(),
        event_bus=MagicMock(),
        worker_id="w",
    )

    assert events == ["session:enter", "db:claim", "db:commit", "session:exit", "net:run"]


async def test_claim_transaction_is_bounded_by_a_ten_second_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hanging claim fails fast (today: Prisma's tx(timeout=10000)) instead of holding the
    row lock and a pooled connection for the whole lease."""
    fake = FakeExecutions(monkeypatch, claim_hangs=True)
    bounds: list[float | None] = []
    real_timeout = asyncio.timeout

    def _short_timeout(delay: float | None) -> Any:
        bounds.append(delay)
        return real_timeout(0.05)

    monkeypatch.setattr(asyncio, "timeout", _short_timeout)

    from src.api.internal import claim_and_run_execution

    with pytest.raises(TimeoutError):
        await claim_and_run_execution(
            "exec-1",
            "run",
            sessionmaker=fake.sessionmaker,
            checkpointer=MagicMock(),
            event_bus=MagicMock(),
            worker_id="w",
        )

    assert bounds == [10]
    assert fake.commits == 0


def test_claim_and_run_claims_and_dispatches_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")  # dev mode: OIDC check skipped
    client, fake = _client_with_fakes(monkeypatch, claim_result=_claimed())

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
        ran.append(execution_id)

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
    assert ran == ["exec-1"]
    assert len(fake.claim_calls) == 1
    assert fake.claim_calls[0]["execution_id"] == "exec-1"


def test_claim_and_run_is_503_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, _fake = _client_with_fakes(monkeypatch)
    del cast("Any", client.app).state.db_sessionmaker

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "database unavailable"


def test_claim_and_run_no_op_when_already_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """SKIP LOCKED means a second (redelivered) request for the same
    execution finds nothing to claim and returns success without re-running
    — this is the actual single-claim guarantee, not Cloud Tasks itself."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, fake = _client_with_fakes(monkeypatch, claim_result=None)  # locked / not eligible

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "already_claimed"
    assert fake.commits == 1  # the (empty) claim transaction still ends cleanly


def test_claimable_statuses_exclude_terminal_rows_and_include_queued() -> None:
    """Regression pin: the claim's eligibility must never include a terminal status (a
    redelivered Cloud Task could otherwise re-run a finished execution once its lease is
    gone) and must include 'queued' (the row a fresh delivery exists to claim). The SQL
    itself is exercised against Postgres by the repo tests and the behaviour oracle."""
    from src.db.repos.executions import _CLAIMABLE  # pyright: ignore[reportPrivateUsage]

    assert _CLAIMABLE == {"run": ("queued",), "resume": ("running",)}
    for statuses in _CLAIMABLE.values():
        assert not {"completed", "failed", "canceled", "waiting_approval"} & set(statuses)


@pytest.mark.parametrize("kind", ["run", "resume"])
def test_claim_eligibility_is_kind_specific(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """The delivery kind selects the claim's eligible status; an ineligible row is acked
    without replay."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, fake = _client_with_fakes(monkeypatch, claim_result=None)
    response = cast("Any", client).post(
        "/internal/claim-and-run", json={"executionId": "exec-1", "kind": kind}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "already_claimed"}
    assert fake.claim_calls[0]["kind"] == kind


@pytest.mark.parametrize("kind", ["run", "resume"])
def test_claim_stamps_attempt_and_derived_lease_from_one_timestamp(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    monkeypatch.setenv("EXECUTION_MAX_RUNTIME_SECONDS", "900")
    monkeypatch.setenv("EXECUTION_SHUTDOWN_GRACE_SECONDS", "47")
    client, fake = _client_with_fakes(
        monkeypatch, claim_result=_claimed({"_resume_decision": "approved"})
    )
    frozen_now = datetime(2026, 11, 17, 12, tzinfo=UTC)
    clock = MagicMock(return_value=frozen_now)
    monkeypatch.setattr("src.api.internal.utcnow", clock)
    monkeypatch.setattr("src.api.internal.LangGraphExecutor.run", AsyncMock())
    monkeypatch.setattr("src.api.internal.LangGraphExecutor.resume", AsyncMock())

    response = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": kind})

    assert response.status_code == 200, response.text
    call = fake.claim_calls[0]
    assert call["worker_id"]
    assert call["now"] == frozen_now
    assert call["lease_expires_at"] == frozen_now + timedelta(seconds=900 + 47)
    assert call["execution_id"] == "exec-1"
    clock.assert_called_once_with()


def test_claim_and_run_resume_dispatches_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1-2 Task 13: the resume decision travels via the claimed row's
    `variables._resume_decision` (stamped by the enqueueing call site
    BEFORE the Cloud Task fires), not via any request-body field —
    enqueue_execution's Cloud Task body is hardcoded to
    {"executionId": ..., "kind": ...} with no room for it. The claim returns the
    variables it locked, so no second read is needed."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, fake = _client_with_fakes(
        monkeypatch, claim_result=_claimed({"_resume_decision": "approved"})
    )

    from src.engine.langgraph_executor import LangGraphExecutor

    resumed: list[tuple[str, str, str | None]] = []

    async def _fake_resume(
        self: LangGraphExecutor,
        execution_id: str,
        decision: str,
        *,
        attempt_id: str | None = None,
    ) -> None:
        from src.telemetry.context import current_correlation

        correlation = current_correlation()
        assert correlation.execution_id == execution_id
        assert correlation.attempt_id == attempt_id
        resumed.append((execution_id, decision, attempt_id))

    monkeypatch.setattr(LangGraphExecutor, "resume", _fake_resume)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "resume"},
    )
    assert resp.status_code == 200, resp.text
    assert resumed == [("exec-1", "approved", "attempt-1")]
    assert len(fake.claim_calls) == 1


def test_claim_and_run_resume_ignores_legacy_decision_field_in_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ClaimAndRunRequest` dropped the `decision` field (P1-2 Task 13) --
    a stray `decision` key in the request body (e.g. a stale caller) must
    be ignored, not consulted; only `variables._resume_decision` on the
    claimed row is authoritative."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, _fake = _client_with_fakes(
        monkeypatch, claim_result=_claimed({"_resume_decision": "rejected"})
    )

    from src.engine.langgraph_executor import LangGraphExecutor

    resumed: list[tuple[str, str, str | None]] = []

    async def _fake_resume(
        self: LangGraphExecutor,
        execution_id: str,
        decision: str,
        *,
        attempt_id: str | None = None,
    ) -> None:
        resumed.append((execution_id, decision, attempt_id))

    monkeypatch.setattr(LangGraphExecutor, "resume", _fake_resume)

    resp = client.post(
        "/internal/claim-and-run",
        json={"executionId": "exec-1", "kind": "resume", "decision": "approved"},
    )
    assert resp.status_code == 200, resp.text
    assert resumed == [("exec-1", "rejected", "attempt-1")]


def test_claim_and_run_resume_without_decision_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `_resume_decision` in the claimed row's variables (e.g. a stale
    or malformed enqueue) is a 422, matching the pre-Task-13 "decision is
    required" error shape."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, _fake = _client_with_fakes(monkeypatch, claim_result=_claimed({}))

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "resume"})
    assert resp.status_code == 422


def test_claim_and_run_rejects_missing_oidc_token_when_service_account_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once CLOUD_TASKS_SERVICE_ACCOUNT is configured (i.e. in production),
    a request with no Authorization header must be rejected — this is the
    actual security boundary the endpoint depends on."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    client, _fake = _client_with_fakes(monkeypatch)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 401


def test_claim_and_run_rejects_invalid_oidc_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    client, _fake = _client_with_fakes(monkeypatch)

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
    client, _fake = _client_with_fakes(monkeypatch)

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
    format), through the guarded `mark_failed_if_running` (Issue 1; the guard
    and the cleared lease columns are pinned by the repo tests and the
    oracle) so the write can't clobber a concurrent transition, and the HTTP
    response must carry a generic client-facing message (Issue 2) — the raw
    exception text is deliberately NOT included in the response body, only in
    the DB `error` field and the application logs."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, fake = _client_with_fakes(monkeypatch, claim_result=_claimed())

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
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

    assert len(fake.updates) == 1
    data = fake.updates[0]
    assert data["execution_id"] == "exec-1"
    assert data["attempt_id"] == "attempt-1"
    assert data["status"] == "failed"
    assert "RuntimeError" in data["error"]
    assert "simulated executor crash" in data["error"]
    assert data["now"] is not None
    assert data["failure_code"] == "execution_error"
    assert fake.commits == 2  # the claim, then the failure write


def test_claim_and_run_crash_in_resume_marks_row_failed_and_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fast-fail guard, resume path: a crash inside `executor.resume()`
    marks the row 'failed' immediately (guarded on status='running') and
    surfaces a generic error response, rather than silently leaving the
    row 'running' for the sweeper to eventually notice, or leaking raw
    exception text into the client-facing response body."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, fake = _client_with_fakes(
        monkeypatch, claim_result=_claimed({"_resume_decision": "approved"})
    )

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_resume(
        self: LangGraphExecutor, execution_id: str, decision: str, *, attempt_id: str | None = None
    ) -> None:
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

    assert len(fake.updates) == 1
    data = fake.updates[0]
    assert data["execution_id"] == "exec-1"
    assert data["attempt_id"] == "attempt-1"
    assert data["status"] == "failed"
    assert "RuntimeError" in data["error"]
    assert "simulated resume crash" in data["error"]
    assert data["now"] is not None
    assert data["failure_code"] == "execution_error"


def test_claim_and_run_crash_does_not_clobber_concurrent_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 Task 13 follow-up, Issue 1: if a concurrent `cancel_execution`
    already flipped this row to 'canceled' by the time the crash handler's
    guarded write runs, the guard matches zero rows (the repo function returns
    False). The handler must NOT then report the crash as if the write had
    succeeded in overwriting a live row — the row's 'canceled' status is
    already the legitimate, correctly-recorded outcome."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, fake = _client_with_fakes(monkeypatch, claim_result=_claimed(), won=False)

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
        raise RuntimeError("simulated executor crash")

    monkeypatch.setattr(LangGraphExecutor, "run", _crashing_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})

    # No 500 here: the row's outcome is already correctly recorded by
    # whatever transition won the race, so this is not an application
    # failure that needs to be reported (and re-reported to Cloud Tasks
    # as a retry-worthy failure) — it's a benign race loss.
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "already_terminal"
    assert len(fake.updates) == 1


def test_claim_and_run_crash_when_the_failure_write_itself_fails_still_reports_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safety net has its own safety net: a DB error while recording the failure is
    logged, never raised over the original crash, and the lease stays for the lease sweeper."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, fake = _client_with_fakes(
        monkeypatch, claim_result=_claimed(), mark_failed_raises=RuntimeError("db down")
    )

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
        raise RuntimeError("simulated executor crash")

    monkeypatch.setattr(LangGraphExecutor, "run", _crashing_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})

    assert resp.status_code == 500
    assert "see application logs" in resp.text
    assert fake.updates == []


def test_crash_handler_runs_in_its_own_session_after_the_claim_session_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering: the claim session closes first; the crash write is a separate short session."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    events: list[str] = []
    client, _fake = _client_with_fakes(monkeypatch, claim_result=_claimed(), events=events)

    from src.engine.langgraph_executor import LangGraphExecutor

    async def _crashing_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
        events.append("net:run")
        raise RuntimeError("boom")

    monkeypatch.setattr(LangGraphExecutor, "run", _crashing_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})

    assert resp.status_code == 500
    assert events == [
        "session:enter",
        "db:claim",
        "db:commit",
        "session:exit",
        "net:run",
        "session:enter",
        "db:mark_failed",
        "db:commit",
        "session:exit",
    ]


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
    fake = FakeExecutions(monkeypatch, claim_result=_claimed())

    from src.api.internal import ClaimAndRunRequest, claim_and_run
    from src.engine.langgraph_executor import LangGraphExecutor

    async def _cancelled_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(LangGraphExecutor, "run", _cancelled_run)

    fake_request = SimpleNamespace(
        client=SimpleNamespace(host="test"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=MagicMock(), event_bus=MagicMock(), db_sessionmaker=fake.sessionmaker
            )
        ),
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
                _oidc=None,
            )
        )

    # The fast-fail guard must not have touched the row at all — this is
    # what proves CancelledError skipped `except Exception` entirely
    # rather than being caught and re-raised after a (wrong) DB write.
    assert fake.updates == []


def test_claim_and_run_accepts_valid_oidc_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    client, _fake = _client_with_fakes(monkeypatch, claim_result=_claimed())

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(
        self: LangGraphExecutor, execution_id: str, *, attempt_id: str | None = None
    ) -> None:
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
