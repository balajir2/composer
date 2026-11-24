"""Group 10: the Postgres-backed token-bucket rate limiter.

``test_concurrent_checks_today_over_admit`` pins TODAY'S BUG (plan decision D3: read, compute,
write without a lock). Task 3 changes it to "N concurrent checks on a bucket with k tokens admit
exactly k". Every other test here is the serial behaviour, which must not change.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    ExecApi,
    Member,
    Phase4aAdapter,
    install_enqueue,
    jbody,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

TINY = 1e-9  # refill per second so small that elapsed time cannot change the arithmetic


async def _bucket(db: Db, route: str, client: str) -> tuple[float, datetime]:
    rows = await db.bucket(route, client)
    assert len(rows) == 1, rows
    return rows[0]


async def test_first_check_creates_bucket_with_capacity_minus_one(
    adapter: Phase4aAdapter, db: Db
) -> None:
    route, client = unique("route"), unique("client")
    by_route, by_client = unique("route"), unique("client")
    await db.seed_bucket(by_route, by_client, 7.0, datetime.now(UTC) - timedelta(seconds=5))
    before_bystander = await db.bucket(by_route, by_client)

    t0 = datetime.now(UTC)
    assert await adapter.rate_check(route, client, 5, TINY) == (True, 0.0)
    tokens, last_refill = await _bucket(db, route, client)
    assert tokens == 4.0
    assert abs((last_refill - t0).total_seconds()) < 5
    assert await db.bucket(by_route, by_client) == before_bystander
    # a different client key or route key is an independent bucket
    assert await adapter.rate_check(route, unique("client"), 5, TINY) == (True, 0.0)
    assert await adapter.rate_check(unique("route"), client, 5, TINY) == (True, 0.0)
    assert (await _bucket(db, route, client))[0] == 4.0


async def test_capacity_checks_are_allowed_then_denied_with_exact_retry_after(
    adapter: Phase4aAdapter, db: Db
) -> None:
    route, client = unique("route"), unique("client")
    for _ in range(3):
        assert await adapter.rate_check(route, client, 3, TINY) == (True, 0.0)
    tokens, _ = await _bucket(db, route, client)
    assert tokens == pytest.approx(0.0, abs=1e-6)
    allowed, retry_after = await adapter.rate_check(route, client, 3, TINY)
    assert allowed is False
    assert retry_after == pytest.approx((1.0 - tokens) / TINY, rel=1e-6)

    # fractional tokens: retry_after is (1 - tokens) / refill_per_second
    other = unique("client")
    await db.seed_bucket(route, other, 0.25, datetime.now(UTC))
    allowed, retry_after = await adapter.rate_check(route, other, 3, TINY)
    assert allowed is False
    assert retry_after == pytest.approx(0.75 / TINY, rel=1e-6)
    allowed, retry_after = await adapter.rate_check(route, other, 3, 0.5)
    assert allowed is False
    assert 1.4 < retry_after <= 1.5  # (1 - 0.25 - a little refill) / 0.5


async def test_a_deny_does_not_write_the_bucket(adapter: Phase4aAdapter, db: Db) -> None:
    route, client = unique("route"), unique("client")
    bystander_client = unique("client")
    await db.seed_bucket(route, client, 0.5, datetime.now(UTC) - timedelta(seconds=30))
    await db.seed_bucket(route, bystander_client, 0.5, datetime.now(UTC) - timedelta(seconds=30))
    before = await db.bucket(route, client)
    before_bystander = await db.bucket(route, bystander_client)
    for _ in range(3):
        allowed, _retry = await adapter.rate_check(route, client, 5, TINY)
        assert allowed is False
        assert await db.bucket(route, client) == before  # tokens AND last_refill unchanged
    assert await db.bucket(route, bystander_client) == before_bystander


async def test_backdated_last_refill_refills_and_is_capped_at_capacity(
    adapter: Phase4aAdapter, db: Db
) -> None:
    route, client = unique("route"), unique("client")
    capped = unique("client")
    bystander = unique("client")
    await db.seed_bucket(route, client, 0.0, datetime.now(UTC) - timedelta(seconds=3))
    await db.seed_bucket(route, capped, 0.0, datetime.now(UTC) - timedelta(seconds=1000))
    await db.seed_bucket(route, bystander, 0.0, datetime.now(UTC) - timedelta(seconds=3))
    before_bystander = await db.bucket(route, bystander)

    t0 = datetime.now(UTC)
    assert await adapter.rate_check(route, client, 5, 1.0) == (True, 0.0)
    tokens, last_refill = await _bucket(db, route, client)
    assert 1.9 < tokens < 2.5  # ~3 refilled, 1 consumed
    assert abs((last_refill - t0).total_seconds()) < 5  # moved to "now" on an allow

    assert await adapter.rate_check(route, capped, 5, 1.0) == (True, 0.0)
    assert (await _bucket(db, route, capped))[0] == 4.0  # min(capacity, ...) - 1, exactly
    assert await db.bucket(route, bystander) == before_bystander


async def test_two_concurrent_first_checks_both_allowed_one_row(
    adapter: Phase4aAdapter, db: Db
) -> None:
    for _ in range(5):
        route, client = unique("route"), unique("client")
        results = await asyncio.gather(
            adapter.rate_check(route, client, 2, TINY), adapter.rate_check(route, client, 2, TINY)
        )
        assert results == [(True, 0.0), (True, 0.0)]
        rows = await db.bucket(route, client)
        assert len(rows) == 1
        assert float(rows[0][0]) == pytest.approx(0.0, abs=1e-6)


async def test_concurrent_checks_today_over_admit(adapter: Phase4aAdapter, db: Db) -> None:
    """D3 bug, pinned: concurrent checks of one bucket read the same tokens and each writes
    tokens - 1, so more than ``capacity`` requests are admitted."""
    over_admitted = 0
    rounds = 6
    for _ in range(rounds):
        route, client = unique("route"), unique("client")
        await db.seed_bucket(route, client, 3.0, datetime.now(UTC))
        results = await asyncio.gather(
            *(adapter.rate_check(route, client, 3, TINY) for _ in range(12))
        )
        allowed = sum(1 for ok, _ in results if ok)
        assert allowed >= 3
        over_admitted += allowed > 3
    assert over_admitted == rounds, f"over-admitted in {over_admitted}/{rounds} rounds"


async def test_route_level_429_with_retry_after(
    member: Member,
    second_member: Member,
    exec_api: ExecApi,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import get_settings

    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    monkeypatch.setenv("RATE_LIMIT_EXECUTIONS_PER_MINUTE", "2")
    get_settings.cache_clear()
    wid = await db.seed_workflow(member, is_public=True)

    for _ in range(2):
        assert (await exec_api.create(member, wid)).status_code == 202
    denied = await exec_api.create(member, wid)
    assert denied.status_code == 429, denied.text
    assert jbody(denied) == {"detail": "Rate limit exceeded. Retry after 30 seconds."}
    assert denied.headers["Retry-After"] == "30"
    assert len(await db.executions_of(wid)) == 2 and len(rec.calls) == 2  # nothing created
    # the bucket is per user: another user is unaffected
    assert (await exec_api.create(second_member, wid)).status_code == 202
    rows = await db.bucket("executions", member.id)
    assert len(rows) == 1 and float(rows[0][0]) < 1.0
