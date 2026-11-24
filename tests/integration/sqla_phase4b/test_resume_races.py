"""Races on the guarded waiting_approval -> running transition (in-app resume and emailed link).

Every test repeats on fresh rows and asserts on the specific rows, never on sweep-wide counts.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient, Response

from tests._rate_limit_reset import reset_rate_limit_buckets
from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    ExecApi,
    Member,
    Phase4aAdapter,
    jbody,
)
from tests.integration.sqla_phase4b.conftest import (
    approvals_of,
    confirm,
    email_token,
    install_resume_enqueue,
    location,
    lost_race,
    not_waiting,
    result_location,
    resume,
    seed_paused,
    snap,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

ROUNDS = 3
EXPIRY_ERROR = (
    "Execution expired waiting for approval after 1h with no decision. "
    "Auto-failed by the approval-timeout sweeper."
)


def won(resp: Response) -> bool:
    """A resume/confirm response that won the transition."""
    if resp.status_code == 303:
        return location(resp) != result_location("invalid")
    return resp.status_code == 200


async def test_five_concurrent_resumes_one_winner(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await seed_paused(db, member)
        bystander, _ = await seed_paused(db, member)
        by_before = await snap(db, bystander)
        results = await asyncio.gather(*(resume(client, member, eid, "approved") for _ in range(5)))
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 409, 409, 409, 409]
        for r in results:
            if r.status_code == 409:
                assert jbody(r)["detail"] in {not_waiting("running"), lost_race(eid)}
        assert len(await approvals_of(db, eid)) == 1
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]
        row = await snap(db, eid)
        assert row["status"] == "running"
        assert row["variables"]["_resume_decision"] == "approved"
        assert row["variables"]["_pending_approval_since"] == since
        assert await snap(db, bystander) == by_before


async def test_five_concurrent_confirms_of_one_token_one_winner(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await seed_paused(db, member)
        token = email_token(eid, "approved", since)
        results = await asyncio.gather(*(confirm(client, token) for _ in range(5)))
        locations = sorted(location(r) for r in results)
        assert all(r.status_code == 303 for r in results)
        assert locations == sorted([result_location("approved")] + [result_location("invalid")] * 4)
        rows = await approvals_of(db, eid)
        assert len(rows) == 1
        assert rows[0]["via_email_link"] is True
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]
        assert (await snap(db, eid))["status"] == "running"


async def test_approve_and_reject_tokens_race(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await seed_paused(db, member)
        approve = email_token(eid, "approved", since)
        reject = email_token(eid, "rejected", since)
        results = await asyncio.gather(
            *(confirm(client, approve) for _ in range(3)),
            *(confirm(client, reject) for _ in range(3)),
        )
        winners = [r for r in results if won(r)]
        assert len(winners) == 1
        decision = {
            result_location("approved"): "approved",
            result_location("rejected"): "rejected",
        }[location(winners[0])]
        rows = await approvals_of(db, eid)
        assert len(rows) == 1
        assert rows[0]["decision"].value == decision
        assert (await snap(db, eid))["variables"]["_resume_decision"] == decision
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]


@pytest.mark.parametrize("per_side", [1, 3])
async def test_in_app_resume_vs_email_confirm(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    per_side: int,
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await seed_paused(db, member)
        token = email_token(eid, "approved", since)
        results = await asyncio.gather(
            *(resume(client, member, eid, "rejected") for _ in range(per_side)),
            *(confirm(client, token) for _ in range(per_side)),
        )
        in_app = results[:per_side]
        email = results[per_side:]
        in_app_wins = [r for r in in_app if won(r)]
        email_wins = [r for r in email if won(r)]
        assert len(in_app_wins) + len(email_wins) == 1
        rows = await approvals_of(db, eid)
        assert len(rows) == 1
        row = await snap(db, eid)
        if in_app_wins:
            assert rows[0]["via_email_link"] is False
            assert rows[0]["decision"].value == "rejected"
            assert row["variables"]["_resume_decision"] == "rejected"
        else:
            assert rows[0]["via_email_link"] is True
            assert rows[0]["decision"].value == "approved"
            assert row["variables"]["_resume_decision"] == "approved"
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]


CANCEL_ERROR = "Execution canceled by user request."


@pytest.mark.parametrize("order", ["resume_then_cancel", "cancel_then_resume", "gathered"])
async def test_resume_vs_cancel(
    client: AsyncClient,
    db: Db,
    member: Member,
    exec_api: ExecApi,
    monkeypatch: pytest.MonkeyPatch,
    order: str,
) -> None:
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, _since = await seed_paused(db, member)
        if order == "resume_then_cancel":
            r = await resume(client, member, eid, "approved")
            c = await exec_api.cancel(member, eid)
        elif order == "cancel_then_resume":
            c = await exec_api.cancel(member, eid)
            r = await resume(client, member, eid, "approved")
        else:
            r, c = await asyncio.gather(
                resume(client, member, eid, "approved"), exec_api.cancel(member, eid)
            )
        assert c.status_code == 200
        if r.status_code != 200:
            assert r.status_code == 409
            assert jbody(r)["detail"] in {not_waiting("canceled"), lost_race(eid)}
        row = await snap(db, eid)
        assert row["status"] == "canceled"
        assert row["error"] == CANCEL_ERROR
        assert row["lease_owner"] is None
        assert len(await approvals_of(db, eid)) == (1 if r.status_code == 200 else 0)
        claim = await exec_api.claim(eid, "resume")
        assert jbody(claim) == {"status": "already_claimed"}
        assert (await snap(db, eid))["status"] == "canceled"


@pytest.mark.parametrize("order", ["confirm_then_cancel", "cancel_then_confirm", "gathered"])
async def test_confirm_vs_cancel(
    client: AsyncClient,
    db: Db,
    member: Member,
    exec_api: ExecApi,
    monkeypatch: pytest.MonkeyPatch,
    order: str,
) -> None:
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await seed_paused(db, member)
        token = email_token(eid, "approved", since)
        if order == "confirm_then_cancel":
            r = await confirm(client, token)
            c = await exec_api.cancel(member, eid)
        elif order == "cancel_then_confirm":
            c = await exec_api.cancel(member, eid)
            r = await confirm(client, token)
        else:
            r, c = await asyncio.gather(confirm(client, token), exec_api.cancel(member, eid))
        assert c.status_code == 200
        assert r.status_code == 303
        assert location(r) in {result_location("approved"), result_location("invalid")}
        row = await snap(db, eid)
        assert row["status"] == "canceled"
        assert row["error"] == CANCEL_ERROR
        assert row["lease_owner"] is None
        assert len(await approvals_of(db, eid)) == (1 if won(r) else 0)
        claim = await exec_api.claim(eid, "resume")
        assert jbody(claim) == {"status": "already_claimed"}
        assert (await snap(db, eid))["status"] == "canceled"


def _two_hours_ago() -> str:
    return (datetime.now(UTC) - timedelta(hours=2)).isoformat()


async def _expired_row(db: Db, member: Member) -> tuple[str, str]:
    return await seed_paused(db, member, since=_two_hours_ago())


async def test_resume_vs_approval_expiry_sweep(
    client: AsyncClient,
    db: Db,
    member: Member,
    adapter: Phase4aAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    # order A: sweep first, then resume
    eid, _ = await _expired_row(db, member)
    await adapter.sweep_approvals(timeout_hours=1, now=datetime.now(UTC))
    row = await snap(db, eid)
    assert row["status"] == "failed"
    assert row["error"] == EXPIRY_ERROR
    r = await resume(client, member, eid, "approved")
    assert r.status_code == 409
    assert jbody(r) == {"detail": not_waiting("failed")}
    assert await approvals_of(db, eid) == []
    assert await snap(db, eid) == row

    # order B: resume first, then sweep
    eid, _ = await _expired_row(db, member)
    assert (await resume(client, member, eid, "approved")).status_code == 200
    running = await snap(db, eid)
    await adapter.sweep_approvals(timeout_hours=1, now=datetime.now(UTC))
    assert await snap(db, eid) == running
    assert running["status"] == "running"

    # gathered
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, _ = await _expired_row(db, member)
        r, _sweep = await asyncio.gather(
            resume(client, member, eid, "approved"),
            adapter.sweep_approvals(timeout_hours=1, now=datetime.now(UTC)),
        )
        row = await snap(db, eid)
        if r.status_code == 200:
            assert row["status"] == "running"
            assert len(await approvals_of(db, eid)) == 1
        else:
            assert r.status_code == 409
            assert row["status"] == "failed"
            assert row["error"] == EXPIRY_ERROR
            assert await approvals_of(db, eid) == []


async def test_confirm_vs_approval_expiry_sweep(
    client: AsyncClient,
    db: Db,
    member: Member,
    adapter: Phase4aAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await _expired_row(db, member)
    token = email_token(eid, "approved", since)
    await adapter.sweep_approvals(timeout_hours=1, now=datetime.now(UTC))
    row = await snap(db, eid)
    assert row["status"] == "failed"
    assert row["error"] == EXPIRY_ERROR
    r = await confirm(client, token)
    assert location(r) == result_location("invalid")
    assert await approvals_of(db, eid) == []
    assert await snap(db, eid) == row

    eid, since = await _expired_row(db, member)
    token = email_token(eid, "approved", since)
    assert location(await confirm(client, token)) == result_location("approved")
    running = await snap(db, eid)
    await adapter.sweep_approvals(timeout_hours=1, now=datetime.now(UTC))
    assert await snap(db, eid) == running

    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await _expired_row(db, member)
        token = email_token(eid, "approved", since)
        r, _sweep = await asyncio.gather(
            confirm(client, token),
            adapter.sweep_approvals(timeout_hours=1, now=datetime.now(UTC)),
        )
        row = await snap(db, eid)
        if won(r):
            assert row["status"] == "running"
            assert len(await approvals_of(db, eid)) == 1
        else:
            assert row["status"] == "failed"
            assert row["error"] == EXPIRY_ERROR
            assert await approvals_of(db, eid) == []
