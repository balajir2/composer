"""Tests for the Postgres NOTIFY wake-up signal (P1-4), sent through the SQLAlchemy pool."""

from unittest.mock import AsyncMock

import pytest

from src.db.repos import events as events_repo
from src.engine.events_notify import NOTIFY_CHANNEL, notify_execution_event
from tests.unit.db_fakes import RecordingSessionmaker


async def test_notify_sends_channel_and_payload_in_one_short_committed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    sm = RecordingSessionmaker(events)
    monkeypatch.setattr("src.engine.events_notify.get_registered_sessionmaker", lambda: sm)

    async def _notify(_session: object, channel: str, payload: str) -> None:
        events.append(f"db:notify:{channel}:{payload}")

    async def _commit() -> None:
        events.append("db:commit")

    monkeypatch.setattr(events_repo, "notify", _notify)
    sm.session.commit.side_effect = _commit

    await notify_execution_event("ex1", seq=5)

    assert NOTIFY_CHANNEL == "composer_execution_events"
    assert events == [
        "session:enter",
        "db:notify:composer_execution_events:ex1:5",
        "db:commit",
        "session:exit",
    ]


async def test_notify_without_a_sessionmaker_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.engine.events_notify.get_registered_sessionmaker", lambda: None)
    with pytest.raises(RuntimeError):
        await notify_execution_event("ex1", seq=1)


async def test_repo_notify_executes_pg_notify() -> None:
    session = AsyncMock()
    await events_repo.notify(session, "chan", "ex1:5")
    statement = session.execute.await_args.args[0]
    params = session.execute.await_args.args[1]
    assert "pg_notify" in str(statement)
    assert params == {"channel": "chan", "payload": "ex1:5"}
