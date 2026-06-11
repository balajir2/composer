"""Project-level pytest fixtures and collection hooks.

Both the `tests/integration/` and `tests/regression/` packages rely on the
`client` and `app` fixtures defined here. Placing them at the top-level
conftest makes them available to all test sub-packages without relying on
`pytest_plugins` in sub-level conftest files (which pytest no longer supports).

Integration-marked tests are skipped at collection time when TEST_DATABASE_URL
is unset, so unit tests remain unaffected.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.main import create_app

load_dotenv()


def pytest_collection_modifyitems(
    config: pytest.Config,  # pyright: ignore[reportUnusedFunction]
    items: list[pytest.Item],
) -> None:
    """Skip integration-marked tests when TEST_DATABASE_URL isn't set.

    Keeps unit tests fully runnable on any developer machine, while the
    integration/regression suites opt into a real Postgres via env var.
    """
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip_marker = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


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
