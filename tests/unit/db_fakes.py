"""Fakes for route unit tests: routes get a FakeSession; repo functions are patched."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI

from src.db.models import UserRole
from src.db.repos.users import UserRecord
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
