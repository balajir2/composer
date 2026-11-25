"""Tests for /executions endpoints."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.errors import AlreadyExists
from src.db.repos import executions as executions_repo
from src.db.repos import users as users_repo
from src.db.repos.executions import ExecutionRecord, ExecutionRef
from src.db.repos.workflows import WorkflowRecord
from src.engine.langgraph_executor import LangGraphExecutor
from src.main import create_app
from src.security.rate_limit import RateLimiter
from tests.unit.db_fakes import (
    FakeSession,
    FakeSessionmaker,
    RecordingSessionmaker,
    WorkflowRepoMocks,
    assert_no_network_inside_session,
    make_user,
    make_workflow,
    patch_workflow_repos,
)


@pytest.fixture(autouse=True)
def _patch_enqueue_execution(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:  # pyright: ignore[reportUnusedFunction]
    """POST /executions now enqueues a real Cloud Task (P1-2) instead of a
    BackgroundTask. Default-patch it to a no-op for every test in this
    module so tests that don't care about enqueueing (most of them) don't
    trip over constructing a real `tasks_v2.CloudTasksAsyncClient()` (which
    resolves ADC credentials this test environment doesn't have). Tests
    that DO care about the enqueue call override this via their own
    `monkeypatch.setattr` on the same fixture-provided monkeypatch
    instance."""
    mock = AsyncMock()
    monkeypatch.setattr("src.api.executions.enqueue_execution", mock)
    return mock


_repos_holder: list[WorkflowRepoMocks] = []
_create_holder: list[AsyncMock] = []


def _create_mock() -> AsyncMock:
    """``executions_repo.create``, which ``LangGraphExecutor.start_execution`` calls."""
    return _create_holder[0]


def _repos() -> WorkflowRepoMocks:
    return _repos_holder[0]


@pytest.fixture(autouse=True)
def _patch_workflow_reads(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """The workflow read goes through a (fake) sessionmaker and the repo; no database here."""
    _repos_holder[:] = [patch_workflow_repos(monkeypatch)]
    _repos().get.return_value = _workflow_row()
    monkeypatch.setattr(users_repo, "get_by_id", AsyncMock(return_value=make_user(id="dev")))
    create = AsyncMock(return_value=_execution_record(status="queued"))
    _create_holder[:] = [create]
    monkeypatch.setattr(executions_repo, "create", create)


class _ExecRepoMocks:
    """The ``executions_repo`` functions the routes call, patched (no database here)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.get: Any = AsyncMock(return_value=_execution_row(status="completed"))
        self.find_key: Any = AsyncMock(return_value=None)
        self.list_refs: Any = AsyncMock(return_value=[])
        self.cancel: Any = AsyncMock(return_value=True)
        self.mark_failed: Any = AsyncMock(return_value=True)

        async def _delete_terminal(_session: object, ids: list[str]) -> list[ExecutionRef]:
            return [ExecutionRef(i, "t-" + i, "completed") for i in ids]

        self.delete_terminal: Any = AsyncMock(side_effect=_delete_terminal)
        monkeypatch.setattr(executions_repo, "get", self.get)
        monkeypatch.setattr(executions_repo, "find_by_idempotency_key", self.find_key)
        monkeypatch.setattr(executions_repo, "list_refs", self.list_refs)
        monkeypatch.setattr(executions_repo, "cancel_if_active", self.cancel)
        monkeypatch.setattr(executions_repo, "mark_enqueue_failed_if_queued", self.mark_failed)
        monkeypatch.setattr(executions_repo, "delete_terminal", self.delete_terminal)


_exec_holder: list[_ExecRepoMocks] = []


def _exec_repo() -> _ExecRepoMocks:
    return _exec_holder[0]


def _session() -> FakeSession:
    """The session every route in this module opens (FakeSessionmaker's shared one)."""
    return _session_holder[0]


_session_holder: list[FakeSession] = []


@pytest.fixture(autouse=True)
def _patch_execution_repo(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    _exec_holder[:] = [_ExecRepoMocks(monkeypatch)]


def _execution_row(status: str = "running", **overrides: Any) -> ExecutionRecord:
    """A repository record (snake_case), as ``executions_repo.get`` returns it."""
    base: dict[str, Any] = {
        "id": "ex1",
        "workflow_id": "wf1",
        "user_id": "dev",
        "status": status,
        "current_node_id": None,
        "node_results": {},
        "variables": {},
        "input": "hi",
        "output": None,
        "error": None,
        "failure_code": None,
        "started_at": datetime(2026, 4, 20, tzinfo=UTC),
        "completed_at": None,
        "thread_id": "t1",
        "original_owner_email": None,
        "idempotency_key": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "running_since": None,
        "delivery_attempts": 0,
    }
    base.update(overrides)
    return ExecutionRecord(**base)


def _execution_record(status: str = "running") -> ExecutionRecord:
    """What the repo returns, as ``start_execution`` hands it to the route."""
    return _execution_row(status=status)


def _workflow_row(**overrides: Any) -> WorkflowRecord:
    """Minimal workflow row that satisfies LangGraphExecutor.run()."""
    base: dict[str, Any] = {
        "id": "wf1",
        "user_id": "dev",
        "is_public": False,
        "name": "Test Workflow",
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {"id": "end", "type": "end", "data": {}},
        ],
        "edges": [{"source": "start", "target": "end"}],
    }
    base.update(overrides)
    return make_workflow(**base)


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    # The Prisma client is only passed through to enqueue_execution (dev-mode fallback);
    # no execution query goes through it any more.
    db = MagicMock()
    # Dev-mode user_id='dev' has no user row by default → role defaults to 'member'
    db.user = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    app.state.db = db
    sessionmaker = FakeSessionmaker()
    _session_holder[:] = [sessionmaker.session]
    app.state.db_sessionmaker = sessionmaker
    app.state.checkpointer = MagicMock()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), db


def test_post_execution_returns_queued() -> None:
    """P1-2: the response now reflects the row's real initial status —
    'queued' — rather than lying about being 'running' before any worker
    has claimed it via /internal/claim-and-run."""
    client, _db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] == "ex1"
    assert body["status"] == "queued"
    _create_mock().assert_awaited_once()


def test_post_execution_enqueues_cloud_task_instead_of_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 full replacement: POST /executions must enqueue a Cloud Task
    (kind='run') rather than scheduling a request-bound BackgroundTask —
    Cloud Run can scale a request-bound background task's instance to zero
    mid-run, which Cloud Tasks' HTTP-push delivery to
    /internal/claim-and-run is immune to (ADR-0033)."""
    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)

    client, _db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert enqueued == [("ex1", "run")]


def test_post_execution_marks_row_failed_when_enqueue_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If enqueue_execution raises after the row already committed as 'queued', the row
    must not be left stuck at 'queued' forever (no sweeper scans it). The endpoint marks it
    'failed' through the status-guarded repo transition (G1) in its own session, commits,
    and returns a handled 500."""

    async def _raise_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        raise RuntimeError("Cloud Tasks unavailable")

    monkeypatch.setattr("src.api.executions.enqueue_execution", _raise_enqueue)

    client, _db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Execution 'ex1' could not be enqueued for processing."

    _exec_repo().mark_failed.assert_awaited_once()
    assert _exec_repo().mark_failed.await_args.args[1] == "ex1"
    kwargs = _exec_repo().mark_failed.await_args.kwargs
    assert kwargs["error"] == (
        "failed to enqueue execution for durable processing: Cloud Tasks unavailable"
    )
    assert isinstance(kwargs["now"], datetime)
    _session().commit.assert_awaited()


def test_post_execution_enqueue_failure_does_not_clobber_concurrent_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client cancels the still-'queued' row while enqueue_execution is mid-flight. The
    guarded transition (status = 'queued') then affects zero rows, so the cancellation is
    left alone; the caller still gets the informative 500 for the enqueue failure."""

    async def _raise_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        raise RuntimeError("Cloud Tasks unavailable")

    monkeypatch.setattr("src.api.executions.enqueue_execution", _raise_enqueue)

    client, _db = _client_with_mock_db()
    _exec_repo().mark_failed.return_value = False
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 500
    assert "ex1" in resp.json()["detail"]
    # The guard lives in the repo function (proven by the oracle's G1 race test); the route
    # must go through it and never write the status itself.
    _exec_repo().mark_failed.assert_awaited_once()


def test_post_execution_enqueue_failure_survives_nested_mark_failed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the mark-failed write itself raises (a DB blip right after the row's own
    successful create), that secondary exception must NOT mask the original, informative
    enqueue-failure HTTPException."""

    async def _raise_enqueue(execution_id: str, *, kind: str, db: object = None) -> None:
        raise RuntimeError("Cloud Tasks unavailable")

    monkeypatch.setattr("src.api.executions.enqueue_execution", _raise_enqueue)

    client, _db = _client_with_mock_db()
    _exec_repo().mark_failed.side_effect = RuntimeError("DB blip")
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 500
    body = resp.json()
    assert "ex1" in body["detail"]
    assert "enqueued" in body["detail"]
    _exec_repo().mark_failed.assert_awaited_once()


def test_post_execution_404_when_workflow_missing() -> None:
    client, _db = _client_with_mock_db()
    _repos().get.return_value = None
    resp = client.post("/executions", json={"workflowId": "ghost", "input": "hi"})
    assert resp.status_code == 404


def test_post_execution_404_for_other_users_private_workflow() -> None:
    """A member cannot execute a private workflow they do not own."""
    client, _db = _client_with_mock_db()
    _repos().get.return_value = _workflow_row(user_id="someone-else")
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 404
    _create_mock().assert_not_awaited()


def test_post_execution_allows_other_users_public_workflow() -> None:
    """Public workflows remain executable by authenticated members."""
    client, _db = _client_with_mock_db()
    _repos().get.return_value = _workflow_row(user_id="someone-else", is_public=True)
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202


def test_post_execution_with_idempotency_key_replays_existing_execution() -> None:
    """A client that retries POST /executions after a network timeout must not get a second
    execution, or side-effecting nodes downstream would fire twice for one request (P1-3)."""
    client, _db = _client_with_mock_db()
    existing = _execution_row(status="completed")
    _exec_repo().find_key.return_value = existing
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "retry-key-1"},
    )
    assert resp.status_code == 202
    assert resp.json()["id"] == existing.id
    _create_mock().assert_not_awaited()
    # The lookup is scoped to the caller: keys are unique per (workflow, user).
    _exec_repo().find_key.assert_awaited_once()
    assert _exec_repo().find_key.await_args.kwargs == {
        "workflow_id": "wf1",
        "user_id": "dev",
        "key": "retry-key-1",
    }


def test_post_execution_allows_an_assignee_of_a_private_workflow() -> None:
    client, _db = _client_with_mock_db()
    _repos().get.return_value = _workflow_row(user_id="someone-else")
    _repos().exists.return_value = True
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    _create_mock().assert_awaited_once()
    assert _repos().exists.await_args.args[1:] == ("wf1", "dev")


def test_post_execution_404_for_a_non_assignee_of_a_private_workflow() -> None:
    client, _db = _client_with_mock_db()
    _repos().get.return_value = _workflow_row(user_id="someone-else")
    _repos().exists.return_value = False
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Workflow 'wf1' not found."
    _repos().exists.assert_awaited_once()


def test_post_execution_404_with_an_idempotency_key_never_looks_the_key_up() -> None:
    """A caller who may not see the workflow must not probe its executions via the key."""
    client, _db = _client_with_mock_db()
    _repos().get.return_value = _workflow_row(user_id="someone-else")
    _repos().exists.return_value = False
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "probe"},
    )
    assert resp.status_code == 404
    _exec_repo().find_key.assert_not_awaited()
    _create_mock().assert_not_awaited()
    _create_mock().assert_not_awaited()


def test_post_execution_admin_runs_without_an_assignment_lookup() -> None:
    from src.security.auth import get_current_role

    client, _db = _client_with_mock_db()
    _repos().get.return_value = _workflow_row(user_id="someone-else")
    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 202
    _repos().exists.assert_not_awaited()


def test_post_execution_owner_and_public_runs_skip_the_assignment_lookup() -> None:
    client, _db = _client_with_mock_db()
    assert client.post("/executions", json={"workflowId": "wf1", "input": "x"}).status_code == 202
    _repos().get.return_value = _workflow_row(user_id="someone-else", is_public=True)
    assert client.post("/executions", json={"workflowId": "wf1", "input": "x"}).status_code == 202
    _repos().exists.assert_not_awaited()


def test_assignment_is_read_in_the_same_short_session_as_the_workflow() -> None:
    events: list[str] = []
    client, _db = _client_with_mock_db()
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]
    _repos().get.return_value = _workflow_row(user_id="someone-else")

    async def _exists(*_a: object, **_k: object) -> bool:
        events.append("assignment")
        return True

    async def _create(*_a: object, **_k: object) -> ExecutionRecord:
        events.append("db:create_execution")
        return _execution_record(status="queued")

    _repos().exists.side_effect = _exists
    _create_mock().side_effect = _create

    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 202
    at = events.index("assignment")
    assert events[at + 1] == "session:exit"
    # The row is created in the executor's own session, opened after this one closed.
    assert events[at + 2 :][:3] == ["session:enter", "db:create_execution", "session:exit"]
    assert_no_network_inside_session(events)


def test_same_key_from_another_user_starts_their_own_execution() -> None:
    """Keys are unique per (workflow, user): another user's use of the key is invisible,
    so this caller gets a fresh execution instead of a replay or a 409."""
    client, _db = _client_with_mock_db()
    _repos().get.return_value = _workflow_row(user_id="someone-else", is_public=True)
    _exec_repo().find_key.return_value = None
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "shared-key"},
    )
    assert resp.status_code == 202
    _create_mock().assert_awaited_once()
    assert _create_mock().call_args.kwargs["idempotency_key"] == "shared-key"
    assert _exec_repo().find_key.await_args.kwargs["user_id"] == "dev"


def test_require_same_caller_is_a_fail_closed_backstop() -> None:
    """The lookup is already per-user, so a foreign row should never come back; if it
    ever does (a regression in the lookup), refuse rather than replay it."""
    from fastapi import HTTPException

    from src.api._idempotency import require_same_caller

    other = _execution_row(status="completed", user_id="someone-else")
    with pytest.raises(HTTPException) as exc:
        require_same_caller(other, "dev")
    assert exc.value.status_code == 409
    require_same_caller(_execution_row(status="completed"), "dev")


def test_foreign_row_from_the_lookup_is_still_refused_with_409() -> None:
    client, _db = _client_with_mock_db()
    _exec_repo().find_key.return_value = _execution_row(status="completed", user_id="someone-else")
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "k"},
    )
    assert resp.status_code == 409
    _create_mock().assert_not_awaited()


def test_idempotency_key_race_lost_relooks_up_scoped_to_the_caller(
    _patch_enqueue_execution: AsyncMock,
) -> None:
    client, _db = _client_with_mock_db()
    mine = _execution_row(status="queued")
    _exec_repo().find_key.side_effect = [None, mine]
    _create_mock().side_effect = AlreadyExists("uq")
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "k"},
    )
    assert resp.status_code == 202
    assert resp.json()["id"] == mine.id
    _patch_enqueue_execution.assert_not_awaited()
    kwargs = [c.kwargs for c in _exec_repo().find_key.await_args_list]
    assert kwargs == [{"workflow_id": "wf1", "user_id": "dev", "key": "k"}] * 2


def _lost_race_client(
    lookups: list[Any],
) -> tuple[TestClient, MagicMock]:
    client, db = _client_with_mock_db()
    _exec_repo().find_key.side_effect = lookups
    _create_mock().side_effect = AlreadyExists("uq")
    return client, db


def test_race_lost_to_a_foreign_row_is_409_and_not_enqueued(
    _patch_enqueue_execution: AsyncMock,
) -> None:
    """Backstop on the race path: a row owned by someone else is never replayed."""
    foreign = _execution_row(status="completed", user_id="someone-else")
    client, _ = _lost_race_client([None, foreign])
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "k"},
    )
    assert resp.status_code == 409
    _patch_enqueue_execution.assert_not_awaited()


def test_unique_violation_with_no_row_for_the_caller_is_409_not_500(
    _patch_enqueue_execution: AsyncMock,
) -> None:
    """Only possible while the DB still has the old per-workflow index (code deployed
    before alembic 0002): the key belongs to another user, the caller has no row."""
    client, _ = _lost_race_client([None, None])
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "k"},
    )
    assert resp.status_code == 409
    assert "idempotency key already used for this workflow" in resp.text
    _patch_enqueue_execution.assert_not_awaited()


def test_post_execution_with_idempotency_key_creates_when_no_existing_match() -> None:
    client, _db = _client_with_mock_db()
    _exec_repo().find_key.return_value = None
    resp = client.post(
        "/executions",
        json={"workflowId": "wf1", "input": "hi", "idempotencyKey": "fresh-key"},
    )
    assert resp.status_code == 202
    _create_mock().assert_awaited_once()
    assert _create_mock().call_args.kwargs["idempotency_key"] == "fresh-key"


def test_post_execution_without_idempotency_key_skips_lookup() -> None:
    """No idempotency lookup without a key. As of P1-2, the row is no longer run
    in-process (Cloud Tasks enqueueing replaces the BackgroundTask)."""
    client, _db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    _exec_repo().find_key.assert_not_awaited()


def test_get_execution_returns_row() -> None:
    client, _ = _client_with_mock_db()
    resp = client.get("/executions/ex1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["threadId"] == "t1"


def test_get_execution_404_when_missing() -> None:
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = None
    resp = client.get("/executions/ghost")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Execution 'ghost' not found."


def test_get_execution_404_for_another_users_execution() -> None:
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(user_id="someone-else")
    resp = client.get("/executions/ex1")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Execution 'ex1' not found."


def test_delete_execution_owner_succeeds() -> None:
    """Owner can delete their own execution: 204 with no body; the row, its checkpoints and
    its events go through ONE repo call and ONE commit (D6)."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="completed")

    resp = client.delete("/executions/ex1")
    assert resp.status_code == 204
    _exec_repo().delete_terminal.assert_awaited_once()
    assert _exec_repo().delete_terminal.await_args.args[1] == ["ex1"]
    _session().commit.assert_awaited_once()


def test_delete_execution_404_when_missing() -> None:
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = None
    resp = client.delete("/executions/ghost")
    assert resp.status_code == 404
    _exec_repo().delete_terminal.assert_not_awaited()


def test_delete_execution_404_when_not_owner() -> None:
    """Non-owner gets 404 (not 403) — same authz pattern as read."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(user_id="someone-else")
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 404
    _exec_repo().delete_terminal.assert_not_awaited()


def test_delete_execution_running_rejected_409() -> None:
    """Deleting a still-running execution would delete its checkpoints out from under the
    in-flight task (P1-6). Reject rather than silently deleting live state."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="running")
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 409
    assert "running" in resp.json()["detail"]
    _exec_repo().delete_terminal.assert_not_awaited()


def test_delete_execution_queued_rejected_409() -> None:
    """A 'queued' row may be in flight to Cloud Tasks or already claimed by a concurrent
    delivery — never safe to delete unconditionally. Behaves like 'running': 409."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="queued")
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 409
    assert "queued" in resp.json()["detail"]
    _exec_repo().delete_terminal.assert_not_awaited()


def test_bulk_delete_skips_queued_executions() -> None:
    """Same guard as the single-delete 409 above, applied to the bulk path."""
    client, _ = _client_with_mock_db()
    _exec_repo().list_refs.return_value = [
        ExecutionRef("ex1", "t1", "completed"),
        ExecutionRef("ex2", "t2", "queued"),
    ]

    resp = client.post(
        "/executions/delete-bulk",
        json={"executionIds": ["ex1", "ex2"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deletedCount"] == 1
    assert body["skippedCount"] == 1
    assert _exec_repo().delete_terminal.await_args.args[1] == ["ex1"]


def test_delete_execution_waiting_approval_rejected_409() -> None:
    """A row paused for approval is active too: 409."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="waiting_approval")
    resp = client.delete("/executions/ex1")
    assert resp.status_code == 409
    assert "waiting_approval" in resp.json()["detail"]
    _exec_repo().delete_terminal.assert_not_awaited()


def test_delete_execution_admin_can_delete_any() -> None:
    """Admin role bypasses ownership check."""
    from src.security.auth import get_current_role

    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="completed", user_id="someone-else")

    client.app.dependency_overrides[get_current_role] = lambda: ("dev", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.delete("/executions/ex1")
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 204
    assert _exec_repo().delete_terminal.await_args.args[1] == ["ex1"]


def test_bulk_delete_owned_ids() -> None:
    """Owner can bulk-delete their own executions by id list: the id list is intersected
    with their own rows (``owner_id``), and deletion is one repo call and one commit."""
    client, _ = _client_with_mock_db()
    _exec_repo().list_refs.return_value = [
        ExecutionRef("ex1", "t1", "completed"),
        ExecutionRef("ex2", "t2", "completed"),
    ]

    resp = client.post(
        "/executions/delete-bulk",
        json={"executionIds": ["ex1", "ex2"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deletedCount"] == 2
    assert body["skippedCount"] == 0
    assert _exec_repo().list_refs.await_args.kwargs == {"ids": ["ex1", "ex2"], "owner_id": "dev"}
    assert _exec_repo().delete_terminal.await_args.args[1] == ["ex1", "ex2"]
    _session().commit.assert_awaited_once()


def test_bulk_delete_skips_active_executions() -> None:
    """A running or waiting_approval row in the requested set is skipped, not deleted —
    same reasoning as the single-delete 409 (P1-6)."""
    client, _ = _client_with_mock_db()
    _exec_repo().list_refs.return_value = [
        ExecutionRef("ex1", "t1", "completed"),
        ExecutionRef("ex2", "t2", "running"),
        ExecutionRef("ex3", "t3", "waiting_approval"),
    ]

    resp = client.post(
        "/executions/delete-bulk",
        json={"executionIds": ["ex1", "ex2", "ex3"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deletedCount"] == 1
    assert body["skippedCount"] == 2
    assert _exec_repo().delete_terminal.await_args.args[1] == ["ex1"]


def test_bulk_delete_skips_non_owned_ids() -> None:
    """Member callers passing other users' ids: those rows never match the owner-scoped
    read, so they are skipped."""
    client, _ = _client_with_mock_db()
    _exec_repo().list_refs.return_value = [ExecutionRef("ex1", "t1", "completed")]

    resp = client.post(
        "/executions/delete-bulk",
        json={"executionIds": ["ex1", "ex2", "ex3"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deletedCount"] == 1
    assert body["skippedCount"] == 2
    assert _exec_repo().list_refs.await_args.kwargs["owner_id"] == "dev"


def test_bulk_delete_all_in_scope_member_rejected() -> None:
    """allInScope=true is admin-only — protects members from
    accidentally wiping their own history."""
    client, _ = _client_with_mock_db()
    resp = client.post("/executions/delete-bulk", json={"allInScope": True})
    assert resp.status_code == 403


def test_bulk_delete_all_in_scope_admin_succeeds() -> None:
    """Admin can wipe everything via allInScope=true (no id or owner narrowing)."""
    from src.security.auth import get_current_role

    client, _ = _client_with_mock_db()
    _exec_repo().list_refs.return_value = [
        ExecutionRef("ex1", "t1", "completed"),
        ExecutionRef("ex2", "t2", "completed"),
    ]

    client.app.dependency_overrides[get_current_role] = lambda: ("admin-id", "admin")  # type: ignore[attr-defined]
    try:
        resp = client.post("/executions/delete-bulk", json={"allInScope": True})
    finally:
        client.app.dependency_overrides.pop(get_current_role, None)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert resp.json() == {"deletedCount": 2, "skippedCount": 0}
    assert _exec_repo().list_refs.await_args.kwargs == {"ids": None, "owner_id": None}


def test_bulk_delete_no_args_rejected() -> None:
    """Empty body → 422.  Don't let a typo at the call site delete
    every execution the caller can see."""
    client, _ = _client_with_mock_db()
    resp = client.post("/executions/delete-bulk", json={})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Provide executionIds or set allInScope=true."
    _exec_repo().list_refs.assert_not_awaited()


def test_cancel_execution_running_succeeds() -> None:
    """P1-6: a running execution can be explicitly canceled. The response carries the
    pre-update row with only status and error replaced (completedAt stays null)."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="running")
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "canceled"
    assert body["error"] == "Execution canceled by user request."
    assert body["completedAt"] is None
    _exec_repo().cancel.assert_awaited_once()
    assert _exec_repo().cancel.await_args.args[1] == "ex1"
    assert _exec_repo().cancel.await_args.kwargs["error"] == "Execution canceled by user request."
    _session().commit.assert_awaited_once()


def test_cancel_execution_queued_succeeds() -> None:
    """A user who cancels immediately after starting an execution — before any worker has
    claimed it — must not get a 409: 'queued' is cancellable."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="queued")
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "canceled"
    _exec_repo().cancel.assert_awaited_once()


def test_cancel_execution_waiting_approval_succeeds() -> None:
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="waiting_approval")
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "canceled"


def test_cancel_execution_completed_rejected_409() -> None:
    """Already-terminal executions can't be canceled."""
    client, _ = _client_with_mock_db()
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Execution is 'completed', not cancellable."
    _exec_repo().cancel.assert_not_awaited()


def test_cancel_execution_404_when_missing() -> None:
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = None
    resp = client.post("/executions/ghost/cancel")
    assert resp.status_code == 404


def test_cancel_execution_404_when_not_owner() -> None:
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="running", user_id="someone-else")
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 404
    _exec_repo().cancel.assert_not_awaited()


def test_cancel_execution_race_loses_when_the_guarded_update_matches_nothing() -> None:
    """Two near-simultaneous cancel requests (or a cancel racing a resume) — only one can
    win the atomic transition; the loser gets the 'no longer cancellable' 409."""
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="running")
    _exec_repo().cancel.return_value = False
    resp = client.post("/executions/ex1/cancel")
    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "Execution 'ex1' is no longer cancellable (already resolved by a concurrent request)."
    )


@pytest.mark.parametrize("failure_code", ["lease_expired", None])
def test_detail_serializes_failure_code(failure_code: str | None) -> None:
    client, _ = _client_with_mock_db()
    _exec_repo().get.return_value = _execution_row(status="failed", failure_code=failure_code)
    response = cast("Any", client).get("/executions/ex1")
    assert response.status_code == 200, response.text
    assert response.json()["failureCode"] == failure_code


def test_workflow_is_read_in_a_short_session_closed_before_the_execution_is_created() -> None:
    events: list[str] = []
    client, _db = _client_with_mock_db()
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]

    async def _get(*_a: object, **_k: object) -> WorkflowRecord:
        events.append("read")
        return _workflow_row()

    async def _create(*_a: object, **_k: object) -> ExecutionRecord:
        events.append("db:create_execution")
        return _execution_record(status="queued")

    _repos().get.side_effect = _get
    _create_mock().side_effect = _create

    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 202
    first_read = events.index("read")
    assert events[first_read - 1 : first_read + 2] == ["session:enter", "read", "session:exit"]
    # The row is created in the executor's own short session, opened after the read's closed.
    assert events[first_read + 2 :][:3] == ["session:enter", "db:create_execution", "session:exit"]
    assert_no_network_inside_session(events)


def test_post_execution_without_a_database_is_503() -> None:
    client, _db = _client_with_mock_db()
    client.app.state.db_sessionmaker = None  # type: ignore[attr-defined]
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "database unavailable"


def test_create_enqueues_outside_any_session_and_marks_failure_in_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    client, _db = _client_with_mock_db()
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]

    async def _enqueue(*_a: object, **_k: object) -> None:
        events.append("net:enqueue")
        raise RuntimeError("boom")

    async def _start(*_a: object, **_k: object) -> ExecutionRecord:
        # The executor opens its own short session; the route must not hold one around it.
        events.append("net:start_execution")
        return _execution_record(status="queued")

    monkeypatch.setattr(LangGraphExecutor, "start_execution", _start)

    async def _mark(*_a: object, **_k: object) -> bool:
        events.append("db:mark_failed")
        return True

    monkeypatch.setattr("src.api.executions.enqueue_execution", _enqueue)
    _exec_repo().mark_failed.side_effect = _mark
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})

    assert resp.status_code == 500
    start = events.index("net:start_execution")
    assert events[start - 1] == "session:exit"
    assert events[start + 1] == "net:enqueue"
    at = events.index("net:enqueue")
    assert events[at - 1] == "net:start_execution"  # the route's own session closed before it
    assert events[at + 1 :] == ["session:enter", "db:mark_failed", "session:exit"]
    assert_no_network_inside_session(events)


def test_cancel_runs_read_and_guarded_update_in_one_short_session() -> None:
    events: list[str] = []
    client, _db = _client_with_mock_db()
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]
    _exec_repo().get.return_value = _execution_row(status="running")

    async def _get(*_a: object, **_k: object) -> ExecutionRecord:
        events.append("db:read")
        return _execution_row(status="running")

    async def _cancel(*_a: object, **_k: object) -> bool:
        events.append("db:cancel")
        return True

    _exec_repo().get.side_effect = _get
    _exec_repo().cancel.side_effect = _cancel
    resp = client.post("/executions/ex1/cancel")

    assert resp.status_code == 200
    assert events[-4:] == ["session:enter", "db:read", "db:cancel", "session:exit"]


def test_delete_runs_authz_read_and_delete_in_one_short_session() -> None:
    events: list[str] = []
    client, _db = _client_with_mock_db()
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]

    async def _get(*_a: object, **_k: object) -> ExecutionRecord:
        events.append("db:read")
        return _execution_row(status="completed")

    async def _delete(*_a: object, **_k: object) -> list[ExecutionRef]:
        events.append("db:delete")
        return [ExecutionRef("ex1", "t1", "completed")]

    _exec_repo().get.side_effect = _get
    _exec_repo().delete_terminal.side_effect = _delete
    resp = client.delete("/executions/ex1")

    assert resp.status_code == 204
    assert events[-4:] == ["session:enter", "db:read", "db:delete", "session:exit"]
