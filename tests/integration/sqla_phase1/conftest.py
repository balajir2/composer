import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db.models import User, UserRole


@dataclass(frozen=True)
class Member:
    id: str
    email: str
    password: str
    headers: dict[str, str]


@pytest.fixture(autouse=True)
def _phase1_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Standalone mode with a known secret and encryption key, mirroring CI."""
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("JWT_SECRET", "ci-test-only-not-a-real-secret")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _register(client: AsyncClient) -> Member:
    email = f"p1-{secrets.token_hex(6)}@example.com"
    password = "correct-horse-battery-staple"
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    body = cast("dict[str, Any]", resp.json())
    return Member(body["id"], email, password, {"Authorization": f"Bearer {body['accessToken']}"})


@pytest_asyncio.fixture
async def sessionmaker_(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", app.state.db_sessionmaker)


@pytest_asyncio.fixture
async def member(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Member]:
    m = await _register(client)
    yield m
    async with sessionmaker_() as s:
        await s.execute(delete(User).where(User.id == m.id))  # api_keys cascade
        await s.commit()


@pytest_asyncio.fixture
async def admin_headers(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[dict[str, str]]:
    m = await _register(client)
    async with sessionmaker_() as s:
        await s.execute(update(User).where(User.id == m.id).values(role=UserRole.ADMIN))
        await s.commit()
    yield m.headers
    async with sessionmaker_() as s:
        await s.execute(delete(User).where(User.id == m.id))
        await s.commit()
