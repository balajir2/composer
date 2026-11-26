"""Shared helper for resetting Postgres-backed rate-limit state in tests.

P1-4 moved the rate limiter from an in-memory, per-process dict
(src/security/rate_limit.py) to Postgres-backed state
(src/security/rate_limit_pg.py, ADR-0033) so limits are correct across
multiple Cloud Run instances. Every route keys its bucket on
`request.client.host`, which is a fixed constant for every test client
(TestClient / httpx ASGITransport) regardless of which test or app
instance made the request. Without a reset, buckets accumulate real
consumption across the *entire* test session against the same shared
Neon test DB, so routes with a low per-minute cap (e.g.
rate_limit_register_per_minute=5) start failing legitimate, unrelated
tests with 429s once enough earlier tests have hit them.

Used by tests/integration/conftest.py and tests/regression/conftest.py
(not the top-level tests/conftest.py) so tests/unit/ -- which never
touches a real database -- isn't slowed down by a real network
round-trip per test. Takes a bare psycopg connection rather than the
`app` fixture deliberately: this must apply uniformly to every test in
tests/integration/ and tests/regression/, including
test_events_ws_hardening.py, which overrides fixtures that depend on the
shared `app` fixture. A fixture with no dependency on `app` can't
interfere with those overrides.
"""

import os

import psycopg

from src.db.url import to_libpq_url


async def reset_rate_limit_buckets() -> None:
    if not os.environ.get("TEST_DATABASE_URL"):
        return
    async with await psycopg.AsyncConnection.connect(
        to_libpq_url(os.environ["TEST_DATABASE_URL"]), autocommit=True, prepare_threshold=None
    ) as conn:
        await conn.execute("DELETE FROM rate_limit_buckets")
