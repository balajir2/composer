import os
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db.models import LlmApiKey, User, UserRole
from src.main import create_app
from src.security.key_sync import PROVIDER_TO_SETTINGS_FIELD


@dataclass(frozen=True)
class Member:
    id: str
    email: str
    password: str
    headers: dict[str, str]
    refresh_token: str


ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z$")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


MakeUser = Callable[..., Awaitable[Member]]


@pytest.fixture
def phase1_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Standalone mode with a known secret and encryption key, mirroring CI."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("JWT_SECRET", "ci-test-only-not-a-real-secret")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def app(phase1_env: None) -> AsyncIterator[FastAPI]:
    """Mirrors tests/conftest.py's `app`, overridden so the phase 1 env (DATABASE_URL, mode,
    secrets) is applied before create_app() reads settings."""
    get_settings.cache_clear()
    _app = create_app()
    async with _app.router.lifespan_context(_app):
        yield _app


async def _register_user(
    client: AsyncClient, *, email: str | None = None, display_name: str | None = None
) -> Member:
    email = email or f"p1-{secrets.token_hex(6)}@example.com"
    password = "correct-horse-battery-staple"
    payload: dict[str, str] = {"email": email, "password": password}
    if display_name is not None:
        payload["displayName"] = display_name
    resp = await client.post("/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    body = cast("dict[str, Any]", resp.json())
    return Member(
        body["id"],
        email,
        password,
        bearer(body["accessToken"]),
        body["refreshToken"],
    )


@pytest_asyncio.fixture
async def sessionmaker_(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", app.state.db_sessionmaker)


@pytest_asyncio.fixture
async def make_user(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[MakeUser]:
    """Factory registering users; every user it made is deleted at teardown."""
    made: list[str] = []

    async def _make(*, email: str | None = None, display_name: str | None = None) -> Member:
        m = await _register_user(client, email=email, display_name=display_name)
        made.append(m.id)
        return m

    yield _make
    async with sessionmaker_() as s:
        for uid in made:
            await s.execute(delete(User).where(User.id == uid))  # api_keys cascade
        await s.commit()


@pytest_asyncio.fixture
async def member(make_user: MakeUser) -> Member:
    return await make_user()


@pytest_asyncio.fixture
async def second_member(make_user: MakeUser) -> Member:
    return await make_user()


@pytest_asyncio.fixture
async def admin(make_user: MakeUser, sessionmaker_: async_sessionmaker[AsyncSession]) -> Member:
    m = await make_user()
    async with sessionmaker_() as s:
        await s.execute(update(User).where(User.id == m.id).values(role=UserRole.ADMIN))
        await s.commit()
    return m


@pytest_asyncio.fixture
async def admin_headers(admin: Member) -> dict[str, str]:
    return admin.headers


@asynccontextmanager
async def preserved_llm_keys(
    sessionmaker_: async_sessionmaker[AsyncSession], providers: list[str]
) -> AsyncGenerator[None, None]:
    """Remove any real llm_api_keys rows for `providers`, then restore them byte for byte.

    Also restores the in-process Settings fields the admin routes overwrite.
    """
    saved: list[dict[str, Any]] = []
    settings = get_settings()
    fields = {p: PROVIDER_TO_SETTINGS_FIELD[p] for p in providers}
    before = {f: getattr(settings, f) for f in fields.values()}
    try:
        # The delete is inside the try so a failure between it and the restore can't lose a row.
        async with sessionmaker_() as s:
            for p in providers:
                row = await s.scalar(select(LlmApiKey).where(LlmApiKey.provider == p))
                if row is not None:
                    saved.append(
                        {c.key: getattr(row, c.key) for c in LlmApiKey.__mapper__.column_attrs}
                    )
            await s.execute(delete(LlmApiKey).where(LlmApiKey.provider.in_(providers)))
            await s.commit()
        yield
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmApiKey).where(LlmApiKey.provider.in_(providers)))
            for cols in saved:
                await s.execute(insert(LlmApiKey).values(**cols))
            await s.commit()
        for f, v in before.items():
            setattr(settings, f, v)
