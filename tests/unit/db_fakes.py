"""Fakes for route unit tests: routes get a FakeSession; repo functions are patched."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from src.db.models import UserRole
from src.db.repos import workflow_assignments as assignments_repo
from src.db.repos import workflows as workflows_repo
from src.db.repos.users import UserRecord
from src.db.repos.workflows import WorkflowRecord
from src.db.session import get_session


class FakeSession:
    """Stands in for AsyncSession. Repo functions are patched, so only these are called."""

    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()


def override_session(app: FastAPI) -> FakeSession:
    session = FakeSession()

    async def _dep() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = _dep
    return session


class FakeSessionmaker:
    """Assign to ``app.state.db_sessionmaker`` (or register it) when code opens its own sessions.

    Only do this in tests that patch every repo call the code makes: setting
    it also switches on the auth dependencies' live user checks.
    """

    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()

    def __call__(self) -> "FakeSessionmaker._Ctx":
        return FakeSessionmaker._Ctx(self.session)

    class _Ctx:
        def __init__(self, session: FakeSession) -> None:
            self._session = session

        async def __aenter__(self) -> FakeSession:
            return self._session

        async def __aexit__(self, *exc: object) -> bool:
            return False


class RecordingSessionmaker:
    """Like FakeSessionmaker, but appends ``session:enter`` / ``session:exit`` to ``events``.

    Tests append their own network and database events to the same list to prove that no
    network call happens while a session is open.
    """

    def __init__(self, events: list[str], session: FakeSession | None = None) -> None:
        self.events = events
        self.session = session or FakeSession()

    def __call__(self) -> "RecordingSessionmaker._Recorded":
        return RecordingSessionmaker._Recorded(self.session, self.events)

    class _Recorded:
        def __init__(self, session: FakeSession, events: list[str]) -> None:
            self._session = session
            self._events = events

        async def __aenter__(self) -> FakeSession:
            self._events.append("session:enter")
            return self._session

        async def __aexit__(self, *exc: object) -> bool:
            self._events.append("session:exit")
            return False


def assert_no_network_inside_session(
    events: list[str], *, allowed_inside: frozenset[str] = frozenset()
) -> None:
    """Every ``net:*`` event must fall outside any open session; sessions must all close.

    ``allowed_inside`` names the one documented exception: a network event that may run inside
    an open session (the OAuth refresh POST), and only after a ``db:lock`` was seen in THAT
    session (the flag resets whenever a session opens or closes).
    """
    open_sessions = 0
    locked = False
    for event in events:
        if event == "session:enter":
            open_sessions += 1
            locked = False
        elif event == "session:exit":
            open_sessions -= 1
            locked = False
        elif event == "db:lock":
            locked = True
        elif event.startswith("net:"):
            if open_sessions == 0:
                continue
            assert event in allowed_inside, f"{event} ran inside an open session: {events}"
            assert locked, f"{event} ran inside a session that holds no advisory lock: {events}"
    assert open_sessions == 0, f"a session was left open: {events}"


@asynccontextmanager
async def fake_scope(session: FakeSession) -> AsyncGenerator[FakeSession]:
    yield session


def make_user(**overrides: Any) -> UserRecord:
    """A UserRecord with sensible defaults; override any field by name."""
    now = datetime(2026, 4, 21, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "u1",
        "email": "alice@example.com",
        "password_hash": "$2b$12$placeholder",
        "display_name": "Alice",
        "role": UserRole.MEMBER,
        "is_active": True,
        "must_change_password": False,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return UserRecord(**base)


def make_workflow(**overrides: Any) -> WorkflowRecord:
    """A WorkflowRecord with defaults; override any field by its snake_case name."""
    now = datetime(2026, 4, 21, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "w1",
        "user_id": "user-42",
        "name": "Sample",
        "description": None,
        "category": None,
        "tags": [],
        "difficulty": None,
        "estimated_time": None,
        "nodes": [],
        "edges": [],
        "version": None,
        "is_template": False,
        "is_public": False,
        "is_production": False,
        "external_slug": None,
        "original_owner_email": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    for key in ("created_at", "updated_at"):
        if isinstance(base[key], str):
            base[key] = datetime.fromisoformat(base[key].replace("Z", "+00:00"))
    return WorkflowRecord(**base)


@dataclass
class WorkflowRepoMocks:
    """The patched ``workflows`` / ``workflow_assignments`` repo functions of one test."""

    get: AsyncMock
    create: AsyncMock
    update: AsyncMock
    delete: AsyncMock
    list_page: AsyncMock
    search: AsyncMock
    exists: AsyncMock


def patch_workflow_repos(monkeypatch: pytest.MonkeyPatch) -> WorkflowRepoMocks:
    """Replace the repo functions the workflow routes call; nothing touches a database."""
    mocks = WorkflowRepoMocks(
        get=AsyncMock(return_value=None),
        create=AsyncMock(return_value=None),
        update=AsyncMock(return_value=None),
        delete=AsyncMock(return_value=True),
        list_page=AsyncMock(return_value=([], 0)),
        search=AsyncMock(return_value=([], 0)),
        exists=AsyncMock(return_value=False),
    )
    for name in ("get", "create", "update", "delete", "list_page", "search"):
        monkeypatch.setattr(workflows_repo, name, getattr(mocks, name))
    monkeypatch.setattr(assignments_repo, "exists", mocks.exists)
    return mocks
