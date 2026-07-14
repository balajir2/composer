"""Tests for POST /internal/claim-and-run (P1-2)."""

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
