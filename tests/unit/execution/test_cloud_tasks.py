"""Tests for the Cloud Tasks enqueue wrapper (P1-2)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.cloud_tasks import enqueue_execution


async def test_enqueue_execution_builds_oidc_http_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "composer-executions")
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://composer.example.com")
    from src.config import get_settings

    get_settings.cache_clear()

    with patch("src.execution.cloud_tasks.tasks_v2.CloudTasksAsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        # CloudTasksAsyncClient.create_task is a coroutine method on the real
        # client -- enqueue_execution awaits it, so the mock must be an
        # AsyncMock (a plain MagicMock's return value is not awaitable).
        # This matches how async DB/client methods are mocked elsewhere in
        # this suite (e.g. tests/unit/api/test_approval_email.py).
        mock_client.create_task = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client
        mock_client.queue_path = MagicMock(
            return_value="projects/test-project/locations/us-central1/queues/composer-executions"
        )

        await enqueue_execution("exec-123", kind="run")

        mock_client.create_task.assert_called_once()
        call_kwargs = mock_client.create_task.call_args.kwargs
        task = call_kwargs["request"]["task"]
        assert task["http_request"]["url"] == "https://composer.example.com/internal/claim-and-run"
        assert task["http_request"]["oidc_token"]["service_account_email"] == (
            "worker@test-project.iam.gserviceaccount.com"
        )

        body = json.loads(task["http_request"]["body"])
        assert body == {"executionId": "exec-123", "kind": "run"}

    get_settings.cache_clear()
