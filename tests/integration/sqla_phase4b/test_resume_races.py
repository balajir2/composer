"""Races on the guarded waiting_approval -> running transition (in-app resume and emailed link).

Every test repeats on fresh rows and asserts on the specific rows, never on sweep-wide counts.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
    race_behind_lock,
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

    # resume lands between the scan of the sweep and its guarded fail: the sweep loses
    eid, _ = await _expired_row(db, member)
    fired: list[Response] = []

    async def _resume_after_scan() -> None:
        if not fired:
            fired.append(await resume(client, member, eid, "approved"))

    await adapter.sweep_approvals(
        timeout_hours=1, now=datetime.now(UTC), after_scan=_resume_after_scan
    )
    assert [r.status_code for r in fired] == [200]
    row = await snap(db, eid)
    assert row["status"] == "running"
    assert row["error"] is None
    assert row["variables"]["_resume_decision"] == "approved"
    assert len(await approvals_of(db, eid)) == 1


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

    eid, since = await _expired_row(db, member)
    token = email_token(eid, "approved", since)
    fired: list[Response] = []

    async def _confirm_after_scan() -> None:
        if not fired:
            fired.append(await confirm(client, token))

    await adapter.sweep_approvals(
        timeout_hours=1, now=datetime.now(UTC), after_scan=_confirm_after_scan
    )
    assert [location(r) for r in fired] == [result_location("approved")]
    row = await snap(db, eid)
    assert row["status"] == "running"
    assert row["error"] is None
    assert row["variables"]["_resume_decision"] == "approved"
    assert len(await approvals_of(db, eid)) == 1


# --------------------------------------------------------------------------- behind the row lock
# Requests are parked between their read and their guarded write (see conftest.hold_row), so a
# port that guards only on the read, or reads then writes, lets two of them through.


async def _cancel_row(holder: AsyncSession, execution_id: str) -> None:
    await holder.execute(
        text("UPDATE workflow_executions SET status = 'canceled' WHERE id = :e"),
        {"e": execution_id},
    )


async def test_two_resumes_past_read_one_winner(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, _ = await seed_paused(db, member)
        results = await race_behind_lock(
            db, eid, [resume(client, member, eid, "approved") for _ in range(2)]
        )
        assert sorted(r.status_code for r in results) == [200, 409]
        loser = next(r for r in results if r.status_code == 409)
        assert jbody(loser) == {"detail": lost_race(eid)}  # past the read: the guard said no
        assert len(await approvals_of(db, eid)) == 1
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]
        assert (await snap(db, eid))["variables"]["_resume_decision"] == "approved"


async def test_two_confirms_past_read_one_winner(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await seed_paused(db, member)
        token = email_token(eid, "approved", since)
        results = await race_behind_lock(db, eid, [confirm(client, token) for _ in range(2)])
        assert sorted(location(r) for r in results) == sorted(
            [result_location("approved"), result_location("invalid")]
        )
        assert len(await approvals_of(db, eid)) == 1
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]

        eid, since = await seed_paused(db, member)
        approve = email_token(eid, "approved", since)
        reject = email_token(eid, "rejected", since)
        results = await race_behind_lock(
            db, eid, [confirm(client, approve), confirm(client, reject)]
        )
        locations = sorted(location(r) for r in results)
        assert result_location("invalid") in locations
        decision_redirects = [x for x in locations if x != result_location("invalid")]
        assert len(decision_redirects) == 1
        rows = await approvals_of(db, eid)
        assert len(rows) == 1
        assert decision_redirects == [result_location(rows[0]["decision"].value)]
        assert (await snap(db, eid))["variables"]["_resume_decision"] == rows[0]["decision"].value
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]


async def test_resume_and_confirm_past_read_one_winner(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    for _ in range(ROUNDS):
        await reset_rate_limit_buckets()
        eid, since = await seed_paused(db, member)
        token = email_token(eid, "approved", since)
        in_app, email = await race_behind_lock(
            db, eid, [resume(client, member, eid, "rejected"), confirm(client, token)]
        )
        assert int(won(in_app)) + int(won(email)) == 1
        rows = await approvals_of(db, eid)
        assert len(rows) == 1
        assert rows[0]["via_email_link"] is won(email)
        assert (await snap(db, eid))["variables"]["_resume_decision"] == rows[0]["decision"].value
        assert [c for c in rec.calls if c[0] == eid] == [(eid, "resume")]


@pytest.mark.parametrize("variant", ["resume", "confirm"])
async def test_cancel_after_read_beats_resume(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    """A cancel committed after the request read the row but before its guarded write wins: no
    transition, no approval row, no enqueue, no stamp."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)

    async def _cancel(holder: AsyncSession) -> None:
        await _cancel_row(holder, eid)

    if variant == "resume":
        (resp,) = await race_behind_lock(
            db, eid, [resume(client, member, eid, "approved")], holder_action=_cancel
        )
        assert resp.status_code == 409
        assert jbody(resp) == {"detail": lost_race(eid)}
    else:
        token = email_token(eid, "approved", since)
        (resp,) = await race_behind_lock(db, eid, [confirm(client, token)], holder_action=_cancel)
        assert location(resp) == result_location("invalid")
    row = await snap(db, eid)
    assert row["status"] == "canceled"
    assert "_resume_decision" not in row["variables"]
    assert row["running_since"] is None
    assert await approvals_of(db, eid) == []
    assert [c for c in rec.calls if c[0] == eid] == []
