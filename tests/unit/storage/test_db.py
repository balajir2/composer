"""Tests for src.storage.db's prisma_lifespan startup/shutdown (P1-4).

Focuses on the shutdown path: prisma_lifespan's `finally` block must close
the dedicated NOTIFY connection (src/engine/events_notify.py) alongside
disconnecting the Prisma client, or it leaks a connection across restarts.
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

    async with prisma_lifespan(app):
        close_notify_mock.assert_not_awaited()

    close_notify_mock.assert_awaited_once()
    mock_prisma.disconnect.assert_awaited_once()


async def test_prisma_lifespan_closes_notify_connection_even_if_disconnect_raises(
    mock_prisma: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTIFY connection cleanup must not depend on db.disconnect() succeeding
    first — it runs before db.disconnect() in the finally block."""
    close_notify_mock = AsyncMock()
    monkeypatch.setattr("src.engine.events_notify.close_notify_connection", close_notify_mock)
    mock_prisma.disconnect = AsyncMock(side_effect=RuntimeError("boom"))

    app = FastAPI()

    with pytest.raises(RuntimeError, match="boom"):
        async with prisma_lifespan(app):
            pass

    close_notify_mock.assert_awaited_once()
