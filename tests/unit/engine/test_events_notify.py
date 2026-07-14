"""Tests for the Postgres LISTEN/NOTIFY wake-up signal (P1-4)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.events_notify import notify_execution_event


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
