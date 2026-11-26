"""Startup against a database that stops answering must fail fast, never hang.

Production 2026-11-26 00:00 UTC: an instance's FastAPI lifespan never finished, so uvicorn never
bound port 8080 and Cloud Run's 240 s TCP startup probe killed it. Every network await in startup
needs an upper bound well under the probe; these tests put a proxy in front of the test database
that goes silent (TCP open, nothing answered) at a chosen point and assert startup raises within
a bound instead of hanging.
"""

import asyncio
import os
import time

import pytest
from fastapi import FastAPI

from src.config import get_settings
from src.main import create_app
from tests.integration.startup._blackhole import blackhole_proxy, via_proxy

pytestmark = pytest.mark.integration

# Far above every bound the tests configure, far below "forever".
HANG_GUARD_SECONDS = 45.0
# Cancelling a query on a silent connection: psycopg waits 5 s for its cancel request, 5 s for
# the server to acknowledge, then drops the connection.
CANCEL_SLACK_SECONDS = 12.0


async def _start(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        pytest.fail("startup must not complete against a silent database")


async def _startup_outcome(app: FastAPI) -> tuple[BaseException, float]:
    """Run the lifespan startup; return what it raised and how long it took. Fail on a hang."""
    t0 = time.monotonic()
    task = asyncio.create_task(_start(app))
    done, _ = await asyncio.wait({task}, timeout=HANG_GUARD_SECONDS)
    if task not in done:
        task.cancel()
        await asyncio.wait({task}, timeout=10)
        pytest.fail(f"startup hung for {HANG_GUARD_SECONDS:.0f}s against a silent database")
    exc = task.exception()
    assert exc is not None
    return exc, time.monotonic() - t0


@pytest.fixture
def proxied_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv("DATABASE_DIRECT_URL", raising=False)
    monkeypatch.setenv("DB_CONNECT_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("CHECKPOINTER_POOL_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("STARTUP_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("EXECUTION_SWEEPER_INTERVAL_SECONDS", "0")
    return monkeypatch


async def _outcome_with_silence_at(
    monkeypatch: pytest.MonkeyPatch, trigger: bytes | None
) -> tuple[BaseException, float, bool]:
    url = os.environ["TEST_DATABASE_URL"]
    async with blackhole_proxy(url, trigger) as proxy:
        monkeypatch.setenv("DATABASE_URL", via_proxy(url, proxy.port))
        get_settings.cache_clear()
        try:
            exc, elapsed = await _startup_outcome(create_app())
        finally:
            get_settings.cache_clear()
        return exc, elapsed, proxy.fired


async def test_schema_check_on_a_silent_connection_fails_fast(
    proxied_env: pytest.MonkeyPatch,
) -> None:
    """The saver's schema-check query goes unanswered (the hang the phase 5 deploy shipped).

    Its own bound (CHECKPOINTER_POOL_TIMEOUT_SECONDS=3) fires, not the 30 s startup deadline.
    psycopg then spends up to ~10 s trying to cancel the query on the silent server.
    """
    exc, elapsed, fired = await _outcome_with_silence_at(proxied_env, b"checkpoint_migrations")
    assert fired, "the connection went silent at the schema check"
    assert isinstance(exc, TimeoutError), repr(exc)
    assert "startup did not finish" not in str(exc)
    assert elapsed < 3 + CANCEL_SLACK_SECONDS, f"took {elapsed:.1f}s"


async def test_llm_key_sync_on_a_silent_connection_hits_the_startup_deadline(
    proxied_env: pytest.MonkeyPatch,
) -> None:
    """A step with no bound of its own (the key-sync query) is caught by the overall deadline."""
    proxied_env.setenv("STARTUP_TIMEOUT_SECONDS", "8")
    exc, elapsed, fired = await _outcome_with_silence_at(proxied_env, b"llm_api_keys")
    assert fired, "the connection went silent at the LLM key sync"
    assert isinstance(exc, TimeoutError), repr(exc)
    assert "startup did not finish within 8s" in str(exc)
    assert "llm key sync" in str(exc)
    assert elapsed < 8 + CANCEL_SLACK_SECONDS, f"took {elapsed:.1f}s"


async def test_a_database_that_never_answers_fails_fast(
    proxied_env: pytest.MonkeyPatch,
) -> None:
    exc, elapsed, _ = await _outcome_with_silence_at(proxied_env, None)
    assert isinstance(exc, Exception), repr(exc)
    assert elapsed < 8, f"bounded by DB_CONNECT_TIMEOUT_SECONDS, took {elapsed:.1f}s"
