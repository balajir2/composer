"""Group 5: POST /executions/{id}/cancel and its races."""

import asyncio

import pytest

from tests.integration.sqla_phase4a.conftest import (
    EXECUTION_KEYS,
    HTTPX_MARK,
    Db,
    ExecApi,
    Member,
    Phase4aAdapter,
    ago,
    jbody,
    not_found,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

CANCEL_ERROR = "Execution canceled by user request."
FINAL_STATE = {
    "variables": {"lastOutput": "done", "k": "v"},
    "node_results": {"s": {"status": "completed"}},
    "final_outputs": {},
}


async def _seed(db: Db, wid: str, owner: Member, status: str) -> str:
    leased = status == "running"
    return await db.seed_execution(
        wid,
        owner,
        status=status,
        variables={"keep": "me"},
        node_results={"n": {"ok": True}},
        input={"in": 1},
        current_node_id="n1",
        running_since=ago(minutes=5) if leased else None,
        lease_owner="worker-1" if leased else None,
        lease_expires_at=ago(minutes=-20) if leased else None,
        delivery_attempts=1 if leased else 0,
    )


@pytest.mark.parametrize("status", ["queued", "running", "waiting_approval"])
async def test_cancel_active_row_exact_response_and_row(
    exec_api: ExecApi, member: Member, db: Db, status: str
) -> None:
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, status)
    bystander = await _seed(db, wid, member, "running")
    before = db.snapshot(await db.must(eid))
    before_bystander = db.snapshot(await db.must(bystander))

    r = await exec_api.cancel(member, eid)
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == EXECUTION_KEYS
    assert body["status"] == "canceled" and body["error"] == CANCEL_ERROR
    # D9 (kept): the response is the pre-update row with only status and error replaced.
    assert body["completedAt"] is None
    assert body["id"] == eid and body["variables"] == {"keep": "me"}
    assert body["nodeResults"] == {"n": {"ok": True}} and body["currentNodeId"] == "n1"

    after = db.snapshot(await db.must(eid))
    changed = {k for k in before if before[k] != after[k]}
    expected = {"status", "error", "completed_at"}
    if status == "running":
        expected |= {"running_since", "lease_owner", "lease_expires_at"}
    assert changed == expected, changed
    assert after["status"] == "canceled" and after["error"] == CANCEL_ERROR
    assert after["completed_at"] is not None and after["completed_at"] > ago(minutes=1)
    assert after["running_since"] is None and after["lease_owner"] is None
    assert after["lease_expires_at"] is None
    assert after["delivery_attempts"] == before["delivery_attempts"]
    assert db.snapshot(await db.must(bystander)) == before_bystander


@pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
async def test_cancel_terminal_row_is_409_and_untouched(
    exec_api: ExecApi, member: Member, db: Db, status: str
) -> None:
    wid = await db.seed_workflow(member)
    eid = await db.seed_execution(
        wid, member, status=status, error="old", completed_at=ago(hours=1)
    )
    before = db.snapshot(await db.must(eid))
    r = await exec_api.cancel(member, eid)
    assert r.status_code == 409
    assert jbody(r) == {"detail": f"Execution is {status!r}, not cancellable."}
    assert db.snapshot(await db.must(eid)) == before


async def test_cancel_authz(
    exec_api: ExecApi, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "queued")
    before = db.snapshot(await db.must(eid))
    r = await exec_api.cancel(second_member, eid)
    assert r.status_code == 404 and jbody(r) == {"detail": not_found(eid)}
    assert db.snapshot(await db.must(eid)) == before
    r = await exec_api.cancel(member, "no-such-execution")
    assert r.status_code == 404 and jbody(r) == {"detail": not_found("no-such-execution")}
    r = await exec_api.cancel(admin, eid)
    assert r.status_code == 200 and jbody(r)["status"] == "canceled"
    assert (await db.must(eid)).status == "canceled"


async def test_five_concurrent_cancels_exactly_one_wins(
    exec_api: ExecApi, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    for _ in range(3):  # repeat: the interleaving differs run to run
        eid = await _seed(db, wid, member, "running")
        responses = await asyncio.gather(*(exec_api.cancel(member, eid) for _ in range(6)))
        codes = sorted(r.status_code for r in responses)
        assert codes == [200] + [409] * 5, [r.text for r in responses]
        details = {jbody(r)["detail"] for r in responses if r.status_code == 409}
        # a late reader sees the terminal row; an early reader loses the guarded update
        allowed = {
            "Execution is 'canceled', not cancellable.",
            f"Execution {eid!r} is no longer cancellable (already resolved by a concurrent request).",
        }
        assert details <= allowed, details
        row = await db.must(eid)
        assert row.status == "canceled" and row.error == CANCEL_ERROR
        assert row.completed_at is not None and row.lease_owner is None


async def test_cancel_then_completion_keeps_canceled(
    exec_api: ExecApi, adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "running")
    assert (await exec_api.cancel(member, eid)).status_code == 200
    canceled = db.snapshot(await db.must(eid))
    assert await adapter.mark_completed(eid, FINAL_STATE) is False
    assert db.snapshot(await db.must(eid)) == canceled
    assert canceled["output"] is None and canceled["status"] == "canceled"


async def test_completion_then_cancel_keeps_completed(
    exec_api: ExecApi, adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "running")
    assert await adapter.mark_completed(eid, FINAL_STATE) is True
    done = db.snapshot(await db.must(eid))
    assert done["status"] == "completed" and done["output"] == "done"
    assert done["variables"] == FINAL_STATE["variables"]
    assert done["node_results"] == FINAL_STATE["node_results"]
    assert done["completed_at"] is not None and done["error"] is None
    assert done["lease_owner"] is None and done["lease_expires_at"] is None
    assert done["running_since"] is None
    r = await exec_api.cancel(member, eid)
    assert r.status_code == 409 and jbody(r) == {
        "detail": "Execution is 'completed', not cancellable."
    }
    assert db.snapshot(await db.must(eid)) == done


async def test_cancel_vs_completion_gathered_has_exactly_one_winner(
    exec_api: ExecApi, adapter: Phase4aAdapter, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    outcomes: set[str] = set()
    for _ in range(8):
        eid = await _seed(db, wid, member, "running")
        cancel_resp, completed = await asyncio.gather(
            exec_api.cancel(member, eid), adapter.mark_completed(eid, FINAL_STATE)
        )
        row = await db.must(eid)
        if cancel_resp.status_code == 200:
            assert completed is False
            assert row.status == "canceled" and row.error == CANCEL_ERROR and row.output is None
            outcomes.add("canceled")
        else:
            assert cancel_resp.status_code == 409, cancel_resp.text
            assert completed is True
            assert row.status == "completed" and row.output == "done" and row.error is None
            outcomes.add("completed")
        assert row.lease_owner is None and row.completed_at is not None
    assert outcomes  # either winner is legal; consistency is what is pinned
