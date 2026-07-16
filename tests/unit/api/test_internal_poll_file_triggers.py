"""Tests for POST /internal/poll-file-triggers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflow = MagicMock()
    db.cloudstorageconnection = MagicMock()
    db.workflowexecution = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    return TestClient(app), db


def _workflow_with_drive_trigger() -> SimpleNamespace:
    return SimpleNamespace(
        id="wf1",
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
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="conn1", userId="user1")
    )

    from datetime import UTC, datetime

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


def test_poll_endpoint_isolates_per_file_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One file's extraction/trigger failure marks it 'error' and continues
    — mirrors composer watch's per-file try/except (design doc §D.4)."""
    client, db = _client_with_mock_db()
    db.workflow.find_many = AsyncMock(return_value=[_workflow_with_drive_trigger()])
    db.cloudstorageconnection.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="conn1", userId="user1")
    )

    from datetime import UTC, datetime

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
