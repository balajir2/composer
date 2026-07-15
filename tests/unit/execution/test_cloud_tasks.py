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


async def test_get_client_second_caller_blocks_until_first_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller must genuinely block on `_client_lock` while another caller
    holds it, and both must resolve to the same cached client once released.

    `tasks_v2.CloudTasksAsyncClient()` is a fully synchronous constructor —
    unlike `events_notify.py`'s `asyncpg.connect()`, it has no `await`
    inside it. That means two `asyncio.gather()`-scheduled `_get_client()`
    calls never actually interleave: the first runs to completion in a
    single scheduler step before the second ever gets a turn, so
    `construct_mock.assert_called_once()` would pass identically even with
    `_client_lock` deleted entirely — that used to be exactly what this
    test asserted, which was false confidence.

    To prove the lock actually serializes concurrent callers, this test
    manually holds `_client_lock` open across a real await point (a
    background task blocked on `asyncio.wait_for`) and asserts a second
    caller is genuinely stuck behind it before releasing.
    """
    construct_mock = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("src.execution.cloud_tasks.tasks_v2.CloudTasksAsyncClient", construct_mock)

    await cloud_tasks_module._client_lock.acquire()  # pyright: ignore[reportPrivateUsage]
    try:
        task = asyncio.create_task(_get_client())

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.05)

        # Still blocked on the lock -- the constructor must not have run yet.
        construct_mock.assert_not_called()
    finally:
        cloud_tasks_module._client_lock.release()  # pyright: ignore[reportPrivateUsage]

    result = await task

    construct_mock.assert_called_once()
    assert result is cloud_tasks_module._client  # pyright: ignore[reportPrivateUsage]


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
