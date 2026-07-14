"""Tests for POST /internal/claim-and-run (P1-2)."""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

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

    `_mark_completed`/`_mark_failed` never clear lease_owner/
    lease_expires_at, so this predicate is the only thing standing
    between a late redelivery and a double-run.
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
        match = re.search(r"status\s+IN\s*\(([^)]+)\)", sql, re.IGNORECASE)
        assert match, "claim query must have a `status IN (...)` predicate"
        allowed = {s.strip().strip("'") for s in match.group(1).split(",")}
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
        match = re.search(r"status\s+IN\s*\(([^)]+)\)", sql, re.IGNORECASE)
        assert match, "claim query must have a `status IN (...)` predicate"
        allowed = {s.strip().strip("'") for s in match.group(1).split(",")}
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


def test_claim_and_run_resume_dispatches_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

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
    assert resumed == [("exec-1", "approved")]


def test_claim_and_run_resume_without_decision_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

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
