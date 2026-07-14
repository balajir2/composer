"""Tests for src.storage.db's prisma_lifespan startup/shutdown (P1-4).

Focuses on the shutdown path: prisma_lifespan's `finally` block must close
the dedicated NOTIFY connection (src/engine/events_notify.py) and the
cached Cloud Tasks client (src/execution/cloud_tasks.py) alongside
disconnecting the Prisma client, or it leaks a connection/transport across
restarts. All three cleanups are independently guarded, so a failure in
any one must never skip the others — verified in multiple directions below.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from src.storage.db import prisma_lifespan


@pytest.fixture
def mock_prisma(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock_db = MagicMock()
    mock_db.connect = AsyncMock()
    mock_db.disconnect = AsyncMock()
    monkeypatch.setattr("src.storage.db.Prisma", lambda: mock_db)
    return mock_db


async def test_prisma_lifespan_closes_notify_connection_on_shutdown(
    mock_prisma: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()

    close_notify_mock = AsyncMock()
    monkeypatch.setattr("src.engine.events_notify.close_notify_connection", close_notify_mock)
    monkeypatch.setattr("src.execution.cloud_tasks.close_cloud_tasks_client", AsyncMock())

    async with prisma_lifespan(app):
        close_notify_mock.assert_not_awaited()

    close_notify_mock.assert_awaited_once()
    mock_prisma.disconnect.assert_awaited_once()


async def test_prisma_lifespan_closes_cloud_tasks_client_on_shutdown(
    mock_prisma: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()

    monkeypatch.setattr("src.engine.events_notify.close_notify_connection", AsyncMock())
    close_cloud_tasks_mock = AsyncMock()
    monkeypatch.setattr(
        "src.execution.cloud_tasks.close_cloud_tasks_client", close_cloud_tasks_mock
    )

    async with prisma_lifespan(app):
        close_cloud_tasks_mock.assert_not_awaited()

    close_cloud_tasks_mock.assert_awaited_once()
    mock_prisma.disconnect.assert_awaited_once()


async def test_prisma_lifespan_closes_notify_connection_even_if_disconnect_raises(
    mock_prisma: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTIFY connection cleanup must still run — and the raised error must not
    propagate out of the context manager — if db.disconnect() raises."""
    close_notify_mock = AsyncMock()
    monkeypatch.setattr("src.engine.events_notify.close_notify_connection", close_notify_mock)
    monkeypatch.setattr("src.execution.cloud_tasks.close_cloud_tasks_client", AsyncMock())
    mock_prisma.disconnect = AsyncMock(side_effect=RuntimeError("boom"))

    app = FastAPI()

    async with prisma_lifespan(app):
        pass

    close_notify_mock.assert_awaited_once()
    mock_prisma.disconnect.assert_awaited_once()


async def test_prisma_lifespan_disconnects_prisma_even_if_close_notify_raises(
    mock_prisma: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """db.disconnect() must still run — and the raised error must not
    propagate out of the context manager — if close_notify_connection() raises.

    This is the reverse-direction case: cleanup order must not matter, so a
    failure in close_notify_connection() (called first) must not skip
    db.disconnect() (called second)."""
    close_notify_mock = AsyncMock(side_effect=RuntimeError("notify boom"))
    monkeypatch.setattr("src.engine.events_notify.close_notify_connection", close_notify_mock)
    monkeypatch.setattr("src.execution.cloud_tasks.close_cloud_tasks_client", AsyncMock())

    app = FastAPI()

    async with prisma_lifespan(app):
        pass

    close_notify_mock.assert_awaited_once()
    mock_prisma.disconnect.assert_awaited_once()


async def test_prisma_lifespan_disconnects_prisma_even_if_close_cloud_tasks_raises(
    mock_prisma: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """db.disconnect() must still run — and the raised error must not
    propagate out of the context manager — if close_cloud_tasks_client()
    raises. Cleanup order must not matter: a failure in
    close_cloud_tasks_client() must not skip db.disconnect()."""
    monkeypatch.setattr("src.engine.events_notify.close_notify_connection", AsyncMock())
    close_cloud_tasks_mock = AsyncMock(side_effect=RuntimeError("cloud tasks boom"))
    monkeypatch.setattr(
        "src.execution.cloud_tasks.close_cloud_tasks_client", close_cloud_tasks_mock
    )

    app = FastAPI()

    async with prisma_lifespan(app):
        pass

    close_cloud_tasks_mock.assert_awaited_once()
    mock_prisma.disconnect.assert_awaited_once()
