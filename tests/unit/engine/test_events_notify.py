"""Tests for the Postgres LISTEN/NOTIFY wake-up signal (P1-4)."""

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.engine.events_notify as events_notify_module
from src.engine.events_notify import (
    _get_notify_connection,  # pyright: ignore[reportPrivateUsage]
    notify_execution_event,
)


@pytest.fixture(autouse=True)
def _reset_notify_connection_global() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Module-level connection cache leaks across tests without this."""
    events_notify_module._notify_connection = None  # pyright: ignore[reportPrivateUsage]
    yield
    events_notify_module._notify_connection = None  # pyright: ignore[reportPrivateUsage]


async def test_notify_sends_channel_and_payload_under_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()

    async def _fake_get_conn() -> MagicMock:
        return fake_conn

    monkeypatch.setattr("src.engine.events_notify._get_notify_connection", _fake_get_conn)

    await notify_execution_event("ex1", seq=5)

    fake_conn.execute.assert_awaited_once()
    call_args = fake_conn.execute.await_args.args
    assert call_args[0].startswith("SELECT pg_notify(")
    # payload is "ex1:5" — well under the 8000-byte NOTIFY cap
    assert "ex1:5" in call_args


async def test_get_notify_connection_concurrent_callers_connect_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two callers racing to establish the connection while it's None must
    not both open a new asyncpg connection — that would orphan one of them
    (a leak). The asyncio.Lock + double-check-inside-the-lock pattern must
    serialize them onto a single connection.

    `_slow_connect` yields control mid-"connect" (via asyncio.sleep(0)) so
    the second gathered coroutine gets a chance to run while the first is
    still connecting — without the lock, it would see the same "needs
    connecting" state and race in with a second real connect() call.
    """

    async def _slow_connect(*_args: object, **_kwargs: object) -> MagicMock:
        await asyncio.sleep(0)
        conn = MagicMock()
        conn.is_closed = MagicMock(return_value=False)
        return conn

    connect_mock = AsyncMock(side_effect=_slow_connect)
    monkeypatch.setattr("src.engine.events_notify.asyncpg.connect", connect_mock)
    monkeypatch.setattr(
        "src.engine.events_notify.get_settings",
        lambda: MagicMock(database_url="postgresql://fake"),
    )

    results = await asyncio.gather(
        _get_notify_connection(),
        _get_notify_connection(),
    )

    connect_mock.assert_awaited_once()
    assert results[0] is results[1]
