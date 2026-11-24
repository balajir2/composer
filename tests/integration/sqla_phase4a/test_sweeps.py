"""Group 9: the stuck-execution sweeper (orphaned, approvals, leases, event retention)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    Phase4aAdapter,
    jbody,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

ORPHAN_ERROR = (
    "Execution remained running without a lease for more than 120s. "
    "Marked failed by the orphaned-running sweeper."
)
LEASE_ERROR = (
    "Execution's processing lease expired without completing. "
    "Marked failed by the lease-expiry sweeper."
)


def approval_error(hours: int) -> str:
    return (
        f"Execution expired waiting for approval after {hours}h with no decision. "
        "Auto-failed by the approval-timeout sweeper."
    )


def now_ms() -> datetime:
    """UTC now at millisecond precision (what the timestamp(3) columns round-trip exactly)."""
    n = datetime.now(UTC)
    return n.replace(microsecond=n.microsecond // 1000 * 1000)


def changed(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {k for k in before if before[k] != after[k]}


# --------------------------------------------------------------------------- orphaned running


async def test_orphaned_running_fails_only_leaseless_rows_past_grace(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    old = now - timedelta(minutes=10)
    older = now - timedelta(minutes=20)
    failing = [
        await db.seed_execution(
            wid, member, status="running", running_since=old, delivery_attempts=1
        ),
        await db.seed_execution(wid, member, status="running", running_since=older),
        await db.seed_execution(
            wid, None, status="running", running_since=now - timedelta(seconds=121)
        ),
    ]
    untouched = [
        await db.seed_execution(
            wid, member, status="running", running_since=now - timedelta(seconds=30)
        ),
        await db.seed_execution(  # exactly at the cutoff: strictly-older only
            wid, member, status="running", running_since=now - timedelta(seconds=120)
        ),
        await db.seed_execution(
            wid,
            member,
            status="running",
            running_since=old,
            lease_owner="w",
            lease_expires_at=now + timedelta(minutes=20),
        ),
        await db.seed_execution(wid, member, status="queued", running_since=old),
        await db.seed_execution(wid, member, status="waiting_approval", running_since=old),
        await db.seed_execution(wid, member, status="completed", running_since=old),
        await db.seed_execution(wid, member, status="running", running_since=None),
    ]
    before = {e: db.snapshot(await db.must(e)) for e in failing + untouched}

    scanned, marked, errored = await adapter.sweep_orphaned(grace_seconds=120, now=now)
    assert (scanned, marked, errored) == (3, 3, 0)

    for eid in failing:
        after = db.snapshot(await db.must(eid))
        assert after["status"] == "failed" and after["failure_code"] == "orphaned_running"
        assert after["error"] == ORPHAN_ERROR
        assert after["completed_at"] == now
        assert after["running_since"] is None and after["lease_owner"] is None
        assert after["lease_expires_at"] is None
        assert changed(before[eid], after) == {
            "status",
            "failure_code",
            "error",
            "completed_at",
            "running_since",
        }
    for eid in untouched:
        assert db.snapshot(await db.must(eid)) == before[eid]


async def test_orphaned_running_rejects_non_positive_grace(adapter: Phase4aAdapter) -> None:
    for bad in (0, -5):
        with pytest.raises(ValueError, match="grace_seconds must be > 0"):
            await adapter.sweep_orphaned(grace_seconds=bad, now=now_ms())


async def test_orphaned_running_renewal_between_scan_and_write_is_not_failed(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    eid = await db.seed_execution(
        wid, member, status="running", running_since=now - timedelta(minutes=10)
    )
    bystander = await db.seed_execution(wid, member, status="queued")
    before_bystander = db.snapshot(await db.must(bystander))

    async def _claim_after_scan() -> None:
        await db.set_execution(
            eid,
            running_since=now,
            lease_owner="renewed-worker",
            lease_expires_at=now + timedelta(minutes=20),
        )

    assert await adapter.sweep_orphaned(
        grace_seconds=120, now=now, after_scan=_claim_after_scan
    ) == (1, 0, 0)
    row = await db.must(eid)
    assert row.status == "running" and row.lease_owner == "renewed-worker"
    assert row.failure_code is None and row.error is None and row.completed_at is None
    assert row.running_since == now
    assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_orphaned_running_completion_between_scan_and_write_is_not_failed(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    eid = await db.seed_execution(
        wid, member, status="running", running_since=now - timedelta(minutes=10)
    )

    async def _complete_after_scan() -> None:
        await db.set_execution(eid, status="completed", completed_at=now, running_since=None)

    assert await adapter.sweep_orphaned(
        grace_seconds=120, now=now, after_scan=_complete_after_scan
    ) == (1, 0, 0)
    row = await db.must(eid)
    assert row.status == "completed" and row.failure_code is None and row.error is None


# --------------------------------------------------------------------------- approvals


def paused(since: Any, **extra: Any) -> dict[str, Any]:
    return {"_pending_approval_node": "ua", "_pending_approval_since": since, "k": [1], **extra}


async def test_approval_timeout_zero_does_nothing_and_negative_is_an_error(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    eid = await db.seed_execution(
        wid,
        member,
        status="waiting_approval",
        variables=paused((now - timedelta(days=9)).isoformat()),
    )
    before = db.snapshot(await db.must(eid))
    assert await adapter.sweep_approvals(timeout_hours=0, now=now) == (0, 0, 0)
    assert db.snapshot(await db.must(eid)) == before
    with pytest.raises(ValueError, match="timeout_hours must be >= 0"):
        await adapter.sweep_approvals(timeout_hours=-1, now=now)


async def test_expired_approvals_fail_only_old_valid_pauses(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()

    def iso(delta: timedelta) -> str:
        return (now - delta).isoformat()

    expiring = [
        await db.seed_execution(
            wid,
            member,
            status="waiting_approval",
            variables=paused(iso(timedelta(hours=5))),
            lease_owner="stale-owner",  # untouched by this sweep
        ),
        await db.seed_execution(
            wid,
            None,
            status="waiting_approval",
            variables=paused(iso(timedelta(hours=2, seconds=1))),
        ),
        await db.seed_execution(
            wid, member, status="waiting_approval", variables=paused(iso(timedelta(days=30)))
        ),
    ]
    untouched = [
        await db.seed_execution(
            wid, member, status="waiting_approval", variables=paused(iso(timedelta(hours=1)))
        ),
        await db.seed_execution(  # exactly at the cutoff: not older than it
            wid, member, status="waiting_approval", variables=paused(iso(timedelta(hours=2)))
        ),
        await db.seed_execution(  # naive timestamp: skipped
            wid, member, status="waiting_approval", variables=paused("2020-01-01T00:00:00")
        ),
        await db.seed_execution(
            wid, member, status="waiting_approval", variables=paused("not-a-date")
        ),
        await db.seed_execution(wid, member, status="waiting_approval", variables=paused(12345)),
        await db.seed_execution(wid, member, status="waiting_approval", variables=paused(None)),
        await db.seed_execution(wid, member, status="waiting_approval", variables={"k": 1}),
        await db.seed_execution(
            wid, member, status="running", variables=paused(iso(timedelta(days=9)))
        ),
        await db.seed_execution(
            wid, member, status="completed", variables=paused(iso(timedelta(days=9)))
        ),
    ]
    before = {e: db.snapshot(await db.must(e)) for e in expiring + untouched}

    scanned, marked, errored = await adapter.sweep_approvals(timeout_hours=2, now=now)
    assert (scanned, marked, errored) == (
        len(expiring) + 7,
        3,
        0,
    )  # every waiting_approval row is scanned

    for eid in expiring:
        after = db.snapshot(await db.must(eid))
        assert after["status"] == "failed" and after["error"] == approval_error(2)
        assert after["completed_at"] == now and after["failure_code"] is None
        assert changed(before[eid], after) == {"status", "error", "completed_at"}
        assert after["variables"] == before[eid]["variables"]
        assert after["lease_owner"] == before[eid]["lease_owner"]
    for eid in untouched:
        assert db.snapshot(await db.must(eid)) == before[eid]


async def test_approval_resumed_or_repaused_between_scan_and_write_is_not_failed(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    old = (now - timedelta(hours=5)).isoformat()
    resumed = await db.seed_execution(wid, member, status="waiting_approval", variables=paused(old))
    repaused = await db.seed_execution(
        wid, member, status="waiting_approval", variables=paused(old)
    )
    still_old = await db.seed_execution(
        wid, member, status="waiting_approval", variables=paused(old)
    )

    async def _race() -> None:
        await db.set_execution(
            resumed, status="running", variables=paused(old, _resume_decision="approved")
        )
        await db.set_execution(repaused, variables=paused(now.isoformat()))

    scanned, marked, errored = await adapter.sweep_approvals(
        timeout_hours=2, now=now, after_scan=_race
    )
    assert (scanned, marked, errored) == (3, 1, 0)
    assert (await db.must(resumed)).status == "running"
    assert (await db.must(resumed)).error is None
    assert (await db.must(repaused)).status == "waiting_approval"
    assert (await db.must(repaused)).variables["_pending_approval_since"] == now.isoformat()
    row = await db.must(still_old)
    assert row.status == "failed" and row.error == approval_error(2)


# --------------------------------------------------------------------------- leases


async def test_expired_leases_fail_only_running_rows_past_their_lease(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    expired = [
        await db.seed_execution(
            wid,
            member,
            status="running",
            lease_owner="dead-1",
            lease_expires_at=now - timedelta(seconds=30),
            running_since=now - timedelta(minutes=31),
            delivery_attempts=1,
        ),
        await db.seed_execution(
            wid,
            None,
            status="running",
            lease_owner="dead-2",
            lease_expires_at=now - timedelta(hours=3),
            running_since=now - timedelta(hours=4),
        ),
        await db.seed_execution(
            wid, member, status="running", lease_expires_at=now - timedelta(milliseconds=1)
        ),
    ]
    untouched = [
        await db.seed_execution(  # exactly now: not strictly expired
            wid, member, status="running", lease_owner="w", lease_expires_at=now
        ),
        await db.seed_execution(
            wid,
            member,
            status="running",
            lease_owner="w",
            lease_expires_at=now + timedelta(minutes=20),
        ),
        await db.seed_execution(wid, member, status="running"),  # no lease: the orphan sweep's job
        await db.seed_execution(
            wid, member, status="queued", lease_expires_at=now - timedelta(hours=1)
        ),
        await db.seed_execution(
            wid, member, status="waiting_approval", lease_expires_at=now - timedelta(hours=1)
        ),
        await db.seed_execution(
            wid, member, status="completed", lease_expires_at=now - timedelta(hours=1)
        ),
    ]
    before = {e: db.snapshot(await db.must(e)) for e in expired + untouched}

    assert await adapter.sweep_leases(now=now) == (3, 3, 0)
    for eid in expired:
        after = db.snapshot(await db.must(eid))
        assert after["status"] == "failed" and after["failure_code"] == "lease_expired"
        assert after["error"] == LEASE_ERROR and after["completed_at"] == now
        assert after["lease_owner"] is None and after["lease_expires_at"] is None
        assert after["running_since"] is None
        assert changed(before[eid], after) <= {
            "status",
            "failure_code",
            "error",
            "completed_at",
            "lease_owner",
            "lease_expires_at",
            "running_since",
        }
    full = db.snapshot(await db.must(expired[0]))
    assert changed(before[expired[0]], full) == {
        "status",
        "failure_code",
        "error",
        "completed_at",
        "lease_owner",
        "lease_expires_at",
        "running_since",
    }
    for eid in untouched:
        assert db.snapshot(await db.must(eid)) == before[eid]


async def test_lease_renewed_between_scan_and_write_is_not_failed(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    renewed = await db.seed_execution(
        wid,
        member,
        status="running",
        lease_owner="dead",
        lease_expires_at=now - timedelta(seconds=30),
    )
    stale = await db.seed_execution(
        wid,
        member,
        status="running",
        lease_owner="dead",
        lease_expires_at=now - timedelta(seconds=40),
    )

    async def _renew() -> None:
        await db.set_execution(
            renewed, lease_owner="renewed", lease_expires_at=now + timedelta(minutes=20)
        )

    assert await adapter.sweep_leases(now=now, after_scan=_renew) == (2, 1, 0)
    row = await db.must(renewed)
    assert row.status == "running" and row.lease_owner == "renewed" and row.failure_code is None
    assert (await db.must(stale)).failure_code == "lease_expired"


async def test_completion_vs_lease_sweep_gathered_has_exactly_one_winner(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    final_state = {"variables": {"lastOutput": "done"}, "node_results": {}, "final_outputs": {}}
    for _ in range(8):
        now = now_ms()
        eid = await db.seed_execution(
            wid,
            member,
            status="running",
            lease_owner="w",
            lease_expires_at=now - timedelta(seconds=30),
            running_since=now - timedelta(minutes=30),
        )
        sweep, completed = await asyncio.gather(
            adapter.sweep_leases(now=now), adapter.mark_completed(eid, final_state)
        )
        row = await db.must(eid)
        if sweep[1] == 1:
            assert completed is False
            assert row.status == "failed" and row.failure_code == "lease_expired"
            assert row.output is None
        else:
            assert sweep == (1, 0, 0), sweep
            assert completed is True
            assert row.status == "completed" and row.output == "done" and row.failure_code is None
        # clear it so the next round's sweep sees exactly its own row
        await db.set_execution(eid, status="completed")


# --------------------------------------------------------------------------- retention


async def test_event_retention_deletes_only_strictly_older_events(
    adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    now = now_ms()
    e1 = await db.seed_execution(wid, member, status="completed")
    e2 = await db.seed_execution(wid, member, status="running")
    cutoff = now - timedelta(days=30)
    await db.add_events(
        e1,
        [(1, "workflow_started", {}), (2, "node_started", {})],
        created_at=now - timedelta(days=40),
    )
    await db.add_events(e1, [(3, "node_completed", {})], created_at=cutoff - timedelta(seconds=1))
    await db.add_events(e1, [(4, "workflow_completed", {"status": "completed"})], created_at=cutoff)
    await db.add_events(e2, [(1, "workflow_started", {})], created_at=cutoff - timedelta(days=100))
    await db.add_events(e2, [(2, "node_started", {})], created_at=cutoff + timedelta(seconds=1))
    await db.add_events(e2, [(3, "node_completed", {})], created_at=now)

    assert await adapter.sweep_events(retention_days=30, now=now) == 4
    assert await db.event_seqs(e1) == [4]
    assert await db.event_seqs(e2) == [2, 3]
    assert await adapter.sweep_events(retention_days=30, now=now) == 0
    # a shorter window reaches the newer rows too
    assert (
        await adapter.sweep_events(retention_days=10, now=now) == 2
    )  # the two at/after cutoff, before now-10d
    assert await db.event_seqs(e2) == [3]


async def test_event_retention_rejects_non_positive_days(adapter: Phase4aAdapter) -> None:
    for bad in (0, -3):
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await adapter.sweep_events(retention_days=bad, now=now_ms())


# --------------------------------------------------------------------------- POST /internal/sweep


async def test_internal_sweep_route_returns_four_counts(
    client: AsyncClient, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.config import get_settings

    wid = await db.seed_workflow(member)
    now = datetime.now(UTC)
    orphan = await db.seed_execution(
        wid, member, status="running", running_since=now - timedelta(minutes=10)
    )
    leased = await db.seed_execution(
        wid,
        member,
        status="running",
        lease_owner="dead",
        lease_expires_at=now - timedelta(minutes=1),
    )
    waiting = await db.seed_execution(
        wid,
        member,
        status="waiting_approval",
        variables=paused((now - timedelta(hours=5)).isoformat()),
    )
    keep = await db.seed_execution(wid, member, status="queued")
    before_keep = db.snapshot(await db.must(keep))
    await db.add_events(keep, [(1, "workflow_started", {})], created_at=now - timedelta(days=60))
    await db.add_events(keep, [(2, "node_started", {})], created_at=now)

    r = await client.post("/internal/sweep")
    assert r.status_code == 200, r.text
    # approval expiry is off by default (approval_wait_timeout_hours = 0)
    assert jbody(r) == {
        "orphaned_running": 1,
        "approvals_expired": 0,
        "leases_expired": 1,
        "events_deleted": 1,
    }
    assert (await db.must(orphan)).failure_code == "orphaned_running"
    assert (await db.must(leased)).failure_code == "lease_expired"
    assert (await db.must(waiting)).status == "waiting_approval"
    assert await db.event_seqs(keep) == [2]
    assert db.snapshot(await db.must(keep)) == before_keep

    monkeypatch.setenv("APPROVAL_WAIT_TIMEOUT_HOURS", "2")
    get_settings.cache_clear()
    r = await client.post("/internal/sweep")
    assert jbody(r) == {
        "orphaned_running": 0,
        "approvals_expired": 1,
        "leases_expired": 0,
        "events_deleted": 0,
    }
    row = await db.must(waiting)
    assert row.status == "failed" and row.error == approval_error(2)
