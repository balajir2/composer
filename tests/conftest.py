"""Project-level pytest fixtures and collection hooks.

Both the `tests/integration/` and `tests/regression/` packages rely on the
`client` and `app` fixtures defined here. Placing them at the top-level
conftest makes them available to all test sub-packages without relying on
`pytest_plugins` in sub-level conftest files (which pytest no longer supports).

Integration-marked tests are skipped at collection time when TEST_DATABASE_URL
is unset, so unit tests remain unaffected.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.config import get_settings
from src.main import create_app

load_dotenv()


if sys.platform == "win32":
    # Defined only on Windows: elsewhere pytest-asyncio's default policy is right,
    # and asyncio.get_event_loop_policy() is deprecated on newer Pythons.

    @pytest.fixture(scope="session")
    def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:  # pyright: ignore[reportUnusedFunction]
        """psycopg's async mode cannot use Windows' default Proactor loop; use the selector loop.

        Without this, every SQLAlchemy integration test fails locally on Windows
        ("Psycopg cannot use the 'ProactorEventLoop'"). Linux CI is unaffected.
        """
        return asyncio.WindowsSelectorEventLoopPolicy()


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
async def app(request: pytest.FixtureRequest) -> AsyncIterator[FastAPI]:
    # The app reads DATABASE_URL; point it at the test DB for this process.
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    # Settings are cached at import time by several modules; drop the cache so
    # the lifespan's SQLAlchemy engine sees the test URL (Prisma reads env directly).
    get_settings.cache_clear()
    _app = create_app()
    async with _app.router.lifespan_context(_app):
        if getattr(request.module, "NEEDS_LEGACY_PRISMA", False):
            # The app no longer has a Prisma client (phase 5). Older integration modules that
            # still seed and inspect rows through Prisma's query API set NEEDS_LEGACY_PRISMA and
            # get a test-only client on ``app.state.db``. Phase 6 rewrites them onto SQLAlchemy and
            # removes this.
            from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

            prisma_client = Prisma()
            await prisma_client.connect()
            _app.state.db = prisma_client
            try:
                yield _app
            finally:
                await prisma_client.disconnect()
                del _app.state.db
        else:
            yield _app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
