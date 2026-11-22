"""Tests for POST /internal/poll-file-triggers."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.db.repos import cloud_storage as cloud_storage_repo
from src.db.repos.cloud_storage import CloudStorageConnectionRecord
from src.main import create_app
from tests.unit.db_fakes import RecordingSessionmaker, assert_no_network_inside_session


def _connection_record(user_id: str) -> CloudStorageConnectionRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return CloudStorageConnectionRecord(
        id="conn1",
        user_id=user_id,
        provider="google-drive",
        account_email="a@example.com",
        encrypted_access_token="enc",
        encrypted_refresh_token=None,
        expires_at=None,
        scope=None,
        created_at=now,
        updated_at=now,
    )


def _patch_connection(monkeypatch: pytest.MonkeyPatch, user_id: str) -> AsyncMock:
    get = AsyncMock(return_value=_connection_record(user_id))
    monkeypatch.setattr(cloud_storage_repo, "get", get)
    return get


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.workflowexecution = MagicMock()
    app.state.db = db
    app.state.db_sessionmaker = RecordingSessionmaker([])
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    return TestClient(app), db


def _workflow_with_drive_trigger(workflow_id: str = "wf1", owner: str = "user1") -> SimpleNamespace:
    # `owner` matches the `userId` the tests' fake CloudStorageConnection
    # returns ("user1") by default — the ownership guard (P1-2 review,
    # Critical #1) requires workflow.userId == connection.userId, so most
    # tests need this workflow/connection pair to already agree; the
    # ownership-mismatch test overrides `owner` to something else.
    return SimpleNamespace(
        id=workflow_id,
        userId=owner,
        isProduction=True,
        nodes=[
            {
                "id": "ft1",
                "type": "file-trigger",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Watch Folder",
                    "provider": "google-drive",
                    "connectionId": "conn1",
                    "driveFolderId": "folder123",
                    "targetInputVariable": "file_content",
                },
            },
        ],
    )


def _workflow_with_malformed_node(
    workflow_id: str = "wf-bad", owner: str = "user1"
) -> SimpleNamespace:
    """A file-trigger node whose `data` is not a dict — `data.get(...)`
    raises AttributeError, exercising the per-node try/except added for
    Critical #2 (a malformed node must not abort the rest of the poll)."""
    return SimpleNamespace(
        id=workflow_id,
        userId=owner,
        isProduction=True,
        nodes=[
            {
                "id": "ft-bad",
                "type": "file-trigger",
                "position": {"x": 0, "y": 0},
                "data": "not-a-dict",
            },
        ],
    )


def test_poll_endpoint_rejects_unauthenticated_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CLOUD_TASKS_SERVICE_ACCOUNT", "scheduler@test-project.iam.gserviceaccount.com"
    )
    # This is the first test in the file, so — unlike test_internal.py's and
    # test_internal_sweep.py's equivalent OIDC-required tests, which happen
    # to run after earlier tests in their own files have already exercised
    # tests/unit/conftest.py's autouse cache-clearing teardown — nothing has
    # cleared src.config.get_settings's lru_cache yet when this test runs
    # standalone (or first in the whole session). Without an explicit clear
    # here, a module imported at collection time that reads get_settings()
    # eagerly (src/executors/http.py's module-level
    # `_MAX_RESPONSE_BYTES = get_settings().http_node_max_response_bytes`)
    # can leave a stale, service-account-less Settings cached, which would
    # make `_verify_internal_oidc` silently skip the OIDC check below.
    # Matches the precedent in test_cloud_storage_oauth.py's
    # `_set_encryption_key` helper: setenv, then cache_clear, before the app
    # is constructed.
    from src.config import get_settings

    get_settings.cache_clear()
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(side_effect=AssertionError("should not be called"))

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 401


def test_poll_endpoint_processes_new_file_and_marks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(return_value=[_workflow_with_drive_trigger()])
    _patch_connection(monkeypatch, "user1")

    from src.storage_providers.base import FileRef

    fake_provider = MagicMock()
    fake_provider.list_new_files = AsyncMock(
        return_value=[
            FileRef(identifier="f1", name="notes.txt", size_bytes=5, modified_at=datetime.now(UTC))
        ]
    )
    fake_provider.read_file = AsyncMock(return_value=b"hello")
    fake_provider.move_file = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "src.api.internal.GoogleDriveProvider", MagicMock(return_value=fake_provider)
    )
    monkeypatch.setattr(
        "src.api.internal.get_valid_drive_access_token", AsyncMock(return_value="at-1")
    )

    fake_execution = SimpleNamespace(id="exec1")
    monkeypatch.setattr(
        "src.engine.langgraph_executor.LangGraphExecutor.start_execution",
        AsyncMock(return_value=fake_execution),
    )
    monkeypatch.setattr("src.api.internal.enqueue_execution", AsyncMock(return_value=None))

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"triggered": 1, "failed": 0}
    fake_provider.move_file.assert_awaited_once_with(
        fake_provider.list_new_files.return_value[0], "processed"
    )


def test_poll_endpoint_passes_processed_and_error_folder_ids_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A node configured with driveProcessedFolderId/driveErrorFolderId must
    have both forwarded into GoogleDriveProvider's constructor so move_file
    can visibly relocate files, not just set the appProperties marker."""
    client, db = _client_with_mock_db()
    workflow = _workflow_with_drive_trigger()
    workflow.nodes[0]["data"]["driveProcessedFolderId"] = "done-folder"
    workflow.nodes[0]["data"]["driveErrorFolderId"] = "err-folder"
    db.workflow.find_many = AsyncMock(return_value=[workflow])
    _patch_connection(monkeypatch, "user1")

    from src.storage_providers.base import FileRef

    fake_provider = MagicMock()
    fake_provider.list_new_files = AsyncMock(
        return_value=[
            FileRef(identifier="f1", name="notes.txt", size_bytes=5, modified_at=datetime.now(UTC))
        ]
    )
    fake_provider.read_file = AsyncMock(return_value=b"hello")
    fake_provider.move_file = AsyncMock(return_value=None)
    provider_cls = MagicMock(return_value=fake_provider)
    monkeypatch.setattr("src.api.internal.GoogleDriveProvider", provider_cls)
    monkeypatch.setattr(
        "src.api.internal.get_valid_drive_access_token", AsyncMock(return_value="at-1")
    )

    fake_execution = SimpleNamespace(id="exec1")
    monkeypatch.setattr(
        "src.engine.langgraph_executor.LangGraphExecutor.start_execution",
        AsyncMock(return_value=fake_execution),
    )
    monkeypatch.setattr("src.api.internal.enqueue_execution", AsyncMock(return_value=None))

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    provider_cls.assert_called_once_with(
        "at-1", processed_folder_id="done-folder", error_folder_id="err-folder"
    )


def test_poll_endpoint_isolates_per_file_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One file's extraction/trigger failure marks it 'error' and continues
    — mirrors composer watch's per-file try/except (design doc §D.4)."""
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(return_value=[_workflow_with_drive_trigger()])
    _patch_connection(monkeypatch, "user1")

    from src.storage_providers.base import FileRef

    fake_provider = MagicMock()
    fake_provider.list_new_files = AsyncMock(
        return_value=[
            FileRef(identifier="f1", name="image.png", size_bytes=5, modified_at=datetime.now(UTC))
        ]
    )
    fake_provider.read_file = AsyncMock(return_value=b"\x89PNG")  # unsupported extension
    fake_provider.move_file = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "src.api.internal.GoogleDriveProvider", MagicMock(return_value=fake_provider)
    )
    monkeypatch.setattr(
        "src.api.internal.get_valid_drive_access_token", AsyncMock(return_value="at-1")
    )

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"triggered": 0, "failed": 1}
    fake_provider.move_file.assert_awaited_once_with(
        fake_provider.list_new_files.return_value[0], "error"
    )


def test_poll_endpoint_skips_node_when_connection_owner_mismatches_workflow_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confused-deputy guard (P1-2 review, Critical #1): a file-trigger
    node's `connectionId` is a plain cuid, not scoped to the workflow's
    owner — a workflow's `nodes` JSON is owner-editable via the Workflow
    CRUD API, so nothing at write time stops workflow owner A from
    pointing `connectionId` at a connection actually owned by user B.
    This endpoint must decline to act on that node (not use B's Drive
    token/identity) rather than silently triggering on B's behalf.

    A skipped-for-ownership-mismatch node counts toward neither
    `triggered` nor `failed`: it's a misconfiguration caught before any
    file is even listed, not a file that was attempted and failed.
    """
    client, db = _client_with_mock_db()
    # Workflow owned by "attacker"; the connection its node references is
    # actually owned by "victim" — these must NOT match.
    workflow = _workflow_with_drive_trigger(owner="attacker")
    db.workflow.find_many = AsyncMock(return_value=[workflow])
    _patch_connection(monkeypatch, "victim")

    fake_provider = MagicMock()
    fake_provider.list_new_files = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "src.api.internal.GoogleDriveProvider", MagicMock(return_value=fake_provider)
    )
    fake_get_token = AsyncMock(return_value="at-1")
    monkeypatch.setattr("src.api.internal.get_valid_drive_access_token", fake_get_token)
    fake_start_execution = AsyncMock()
    monkeypatch.setattr(
        "src.engine.langgraph_executor.LangGraphExecutor.start_execution",
        fake_start_execution,
    )
    fake_enqueue = AsyncMock(return_value=None)
    monkeypatch.setattr("src.api.internal.enqueue_execution", fake_enqueue)

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"triggered": 0, "failed": 0}
    # The mismatch must be caught BEFORE any Drive access happens at all
    # — no token fetch, no file listing, no execution, no enqueue.
    fake_get_token.assert_not_awaited()
    fake_provider.list_new_files.assert_not_called()
    fake_start_execution.assert_not_awaited()
    fake_enqueue.assert_not_awaited()


def test_poll_endpoint_isolates_malformed_node_from_other_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed file-trigger node (here: `data` is not a dict, so
    `data.get(...)` raises AttributeError) must not abort the whole poll
    — a second, well-formed workflow's file still gets processed
    normally. Proves the per-node try/except isolation (P1-2 review,
    Critical #2) actually works, not just that it doesn't crash: both
    workflows are polled in the same request, and only the malformed
    one's node is skipped."""
    client, db = _client_with_mock_db()
    bad_workflow = _workflow_with_malformed_node()
    good_workflow = _workflow_with_drive_trigger()
    db.workflow.find_many = AsyncMock(return_value=[bad_workflow, good_workflow])
    _patch_connection(monkeypatch, "user1")

    from src.storage_providers.base import FileRef

    fake_provider = MagicMock()
    fake_provider.list_new_files = AsyncMock(
        return_value=[
            FileRef(identifier="f1", name="notes.txt", size_bytes=5, modified_at=datetime.now(UTC))
        ]
    )
    fake_provider.read_file = AsyncMock(return_value=b"hello")
    fake_provider.move_file = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "src.api.internal.GoogleDriveProvider", MagicMock(return_value=fake_provider)
    )
    monkeypatch.setattr(
        "src.api.internal.get_valid_drive_access_token", AsyncMock(return_value="at-1")
    )

    fake_execution = SimpleNamespace(id="exec1")
    monkeypatch.setattr(
        "src.engine.langgraph_executor.LangGraphExecutor.start_execution",
        AsyncMock(return_value=fake_execution),
    )
    monkeypatch.setattr("src.api.internal.enqueue_execution", AsyncMock(return_value=None))

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    # bad_workflow's malformed node is skipped (logged, not raised);
    # good_workflow's single file is still triggered normally.
    assert resp.json() == {"triggered": 1, "failed": 0}
    fake_provider.move_file.assert_awaited_once_with(
        fake_provider.list_new_files.return_value[0], "processed"
    )


def test_poll_endpoint_closes_the_connection_session_before_drive_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short-session rule: the per-node connection read is closed before the token fetch and
    every Drive call."""
    client, db = _client_with_mock_db()
    events: list[str] = []
    client.app.state.db_sessionmaker = RecordingSessionmaker(events)  # type: ignore[attr-defined]
    db.workflow.find_many = AsyncMock(return_value=[_workflow_with_drive_trigger()])

    async def _get(_session: Any, _connection_id: str) -> CloudStorageConnectionRecord:
        events.append("db:get")
        return _connection_record("user1")

    async def _token(_connection_id: str, _sm: Any) -> str:
        events.append("net:token")
        return "at-1"

    async def _list(_folder: str) -> list[Any]:
        events.append("net:list")
        return []

    monkeypatch.setattr(cloud_storage_repo, "get", _get)
    monkeypatch.setattr("src.api.internal.get_valid_drive_access_token", _token)
    fake_provider = MagicMock()
    fake_provider.list_new_files = _list
    monkeypatch.setattr(
        "src.api.internal.GoogleDriveProvider", MagicMock(return_value=fake_provider)
    )

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    assert events == ["session:enter", "db:get", "session:exit", "net:token", "net:list"]
    assert_no_network_inside_session(events)


def test_poll_endpoint_missing_connection_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(return_value=[_workflow_with_drive_trigger()])
    monkeypatch.setattr(cloud_storage_repo, "get", AsyncMock(return_value=None))
    fake_get_token = AsyncMock(return_value="at-1")
    monkeypatch.setattr("src.api.internal.get_valid_drive_access_token", fake_get_token)

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"triggered": 0, "failed": 0}
    fake_get_token.assert_not_awaited()


def test_poll_endpoint_fails_closed_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    client, db = _client_with_mock_db()
    client.app.state.db_sessionmaker = None  # type: ignore[attr-defined]
    db.workflow.find_many = AsyncMock(side_effect=AssertionError("should not be called"))

    resp = client.post("/internal/poll-file-triggers")

    assert resp.status_code == 503
    assert resp.json() == {"detail": "database unavailable"}
