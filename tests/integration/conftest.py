"""Integration test fixtures.

Uses TEST_DATABASE_URL pointing at a real Postgres (CI service container,
or a developer-provisioned Neon branch). Migrations are applied by CI
before pytest runs; locally the developer runs `uv run prisma migrate deploy`
against TEST_DATABASE_URL first.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.main import create_app


@pytest.fixture(scope="session", autouse=True)
def _require_test_database_url() -> None:  # pyright: ignore[reportUnusedFunction]
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip(
            "TEST_DATABASE_URL not set; integration tests require a real Postgres",
            allow_module_level=True,
        )


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    # The app reads DATABASE_URL; point it at the test DB for this process.
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    _app = create_app()
    async with _app.router.lifespan_context(_app):
        yield _app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
