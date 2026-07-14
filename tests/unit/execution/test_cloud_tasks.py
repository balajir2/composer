"""Tests for the Cloud Tasks enqueue wrapper (P1-2)."""

import asyncio
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.execution.cloud_tasks as cloud_tasks_module
from src.execution.cloud_tasks import (
    _get_client,  # pyright: ignore[reportPrivateUsage]
    close_cloud_tasks_client,
    enqueue_execution,
)


@pytest.fixture(autouse=True)
def _reset_client_global() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Module-level client cache leaks across tests without this."""
    cloud_tasks_module._client = None  # pyright: ignore[reportPrivateUsage]
    yield
    cloud_tasks_module._client = None  # pyright: ignore[reportPrivateUsage]


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


async def test_enqueue_execution_reuses_cached_client_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two enqueue_execution() calls must construct CloudTasksAsyncClient
    only once — a fresh client per call leaks a grpc_asyncio transport."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "composer-executions")
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://composer.example.com")
    from src.config import get_settings

    get_settings.cache_clear()

    with patch("src.execution.cloud_tasks.tasks_v2.CloudTasksAsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.create_task = AsyncMock(return_value=None)
        mock_client.queue_path = MagicMock(
            return_value="projects/test-project/locations/us-central1/queues/composer-executions"
        )
        mock_client_cls.return_value = mock_client

        await enqueue_execution("exec-123", kind="run")
        await enqueue_execution("exec-456", kind="resume")

        mock_client_cls.assert_called_once()
        assert mock_client.create_task.await_count == 2

    get_settings.cache_clear()


async def test_get_client_concurrent_callers_construct_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two callers racing to build the client while it's None must not both
    construct a new CloudTasksAsyncClient — the asyncio.Lock + double-check
    pattern must serialize them onto a single cached client."""

    def _slow_construct(*_args: object, **_kwargs: object) -> MagicMock:
        return MagicMock()

    construct_mock = MagicMock(side_effect=_slow_construct)
    monkeypatch.setattr("src.execution.cloud_tasks.tasks_v2.CloudTasksAsyncClient", construct_mock)

    results = await asyncio.gather(_get_client(), _get_client())

    construct_mock.assert_called_once()
    assert results[0] is results[1]


async def test_close_cloud_tasks_client_closes_transport_and_resets_global() -> None:
    fake_client = MagicMock()
    fake_client.transport.close = AsyncMock()
    cloud_tasks_module._client = fake_client  # pyright: ignore[reportPrivateUsage]

    await close_cloud_tasks_client()

    fake_client.transport.close.assert_awaited_once()
    assert cloud_tasks_module._client is None  # pyright: ignore[reportPrivateUsage]

    # A subsequent _get_client() call must construct a fresh client rather
    # than reusing the closed one.
    with patch("src.execution.cloud_tasks.tasks_v2.CloudTasksAsyncClient") as mock_client_cls:
        new_client = MagicMock()
        mock_client_cls.return_value = new_client

        result = await _get_client()

        mock_client_cls.assert_called_once()
        assert result is new_client


async def test_close_cloud_tasks_client_noop_when_never_created() -> None:
    """Closing before any client was ever constructed must not raise."""
    assert cloud_tasks_module._client is None  # pyright: ignore[reportPrivateUsage]
    await close_cloud_tasks_client()
    assert cloud_tasks_module._client is None  # pyright: ignore[reportPrivateUsage]
