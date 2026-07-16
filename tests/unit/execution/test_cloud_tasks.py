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

        await enqueue_execution("exec-123", kind="run", db=MagicMock())

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

        await enqueue_execution("exec-123", kind="run", db=MagicMock())
        await enqueue_execution("exec-456", kind="resume", db=MagicMock())

        mock_client_cls.assert_called_once()
        assert mock_client.create_task.await_count == 2

    get_settings.cache_clear()


async def test_enqueue_execution_dispatches_in_process_when_service_account_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local dev (and anywhere GCP infra isn't provisioned yet) has no
    CLOUD_TASKS_SERVICE_ACCOUNT configured. Building a real Cloud Task in
    that state either 400s once it reaches Google's API ("service_account_
    email must be set" -- the OIDC token block always includes the field,
    empty or not) or fails outright with DefaultCredentialsError if there's
    no ADC either. enqueue_execution must not attempt the real RPC at all
    in that case -- it dispatches the same claim-and-run logic in-process
    instead (fire-and-forget, matching Cloud Tasks' own "enqueue returns
    immediately" semantics -- the caller must not block on the execution
    actually finishing)."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    from src.config import get_settings

    get_settings.cache_clear()

    with patch("src.execution.cloud_tasks.tasks_v2.CloudTasksAsyncClient") as mock_client_cls:
        captured: dict[str, object] = {}

        async def _fake_claim_and_run_execution(
            execution_id: str,
            kind: str,
            db: object,
            checkpointer: object,
            event_bus: object,
            *,
            worker_id: str,
        ) -> dict[str, str]:
            captured["execution_id"] = execution_id
            captured["kind"] = kind
            captured["db"] = db
            captured["worker_id"] = worker_id
            return {"status": "completed"}

        monkeypatch.setattr(
            "src.api.internal.claim_and_run_execution", _fake_claim_and_run_execution
        )

        created: dict[str, object] = {}

        def _fake_create_task(coro: object) -> MagicMock:
            created["coro"] = coro
            return MagicMock()

        monkeypatch.setattr("src.execution.cloud_tasks.asyncio.create_task", _fake_create_task)

        fake_db = MagicMock()
        await enqueue_execution("exec-1", kind="run", db=fake_db)

        # The real Cloud Tasks client must never be constructed -- there's
        # no service account to mint an OIDC token for, and (in a real
        # deployment, unlike this mocked test) likely no reachable queue
        # either.
        mock_client_cls.assert_not_called()

        assert "coro" in created
        await created["coro"]  # type: ignore[misc]

        assert captured["execution_id"] == "exec-1"
        assert captured["kind"] == "run"
        assert captured["db"] is fake_db

    get_settings.cache_clear()


async def test_enqueue_execution_in_process_fallback_logs_but_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-process fallback is fire-and-forget from enqueue_execution's
    caller's point of view (same as a real Cloud Task's async delivery) --
    a failure inside the dispatched claim-and-run must be logged, not left
    to surface as an "asyncio: Task exception was never retrieved" warning
    with no caller able to observe or handle it."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    from src.config import get_settings

    get_settings.cache_clear()

    async def _raising_claim_and_run_execution(
        execution_id: str,
        kind: str,
        db: object,
        checkpointer: object,
        event_bus: object,
        *,
        worker_id: str,
    ) -> dict[str, str]:
        raise RuntimeError("simulated in-process claim failure")

    monkeypatch.setattr(
        "src.api.internal.claim_and_run_execution", _raising_claim_and_run_execution
    )

    created: dict[str, object] = {}

    def _fake_create_task(coro: object) -> MagicMock:
        created["coro"] = coro
        return MagicMock()

    monkeypatch.setattr("src.execution.cloud_tasks.asyncio.create_task", _fake_create_task)

    await enqueue_execution("exec-1", kind="run", db=MagicMock())

    # Must not raise -- the exception is caught and logged inside the
    # dispatched coroutine itself.
    await created["coro"]  # type: ignore[misc]

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
