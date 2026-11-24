"""Group 12: the approval-resume paths (still Prisma in 4a, phase 4b) hand a row to the claim.

``POST /executions/{id}/resume`` and the emailed-link confirm write ``status``, the lease columns
and ``variables`` through their own code and then enqueue a ``resume`` task; the claim and the
executor (SQLAlchemy after the port) must see exactly what they committed. The default autouse
driver from tests/integration/conftest.py runs claim-and-run inline over ASGI.
"""

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    EXECUTION_KEYS,
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    ExecApi,
    Member,
    approval_graph,
    install_email_recorder,
    install_enqueue,
    jbody,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _paused(
    exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, list[dict[str, object]]]:
    emails = install_email_recorder(monkeypatch)
    nodes, edges = approval_graph()
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    r = await exec_api.create(member, wid, "payload")
    assert r.status_code == 202, r.text
    eid = jbody(r)["id"]
    row = await db.must(eid)
    assert row.status == "waiting_approval", (row.status, row.error)
    assert row.variables["_pending_approval_node"] == "ua"
    return wid, eid, emails


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_resume_route_then_claim_runs_to_completion(
    exec_api: ExecApi,
    member: Member,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    _wid, eid, _emails = await _paused(exec_api, db, member, monkeypatch)
    bystander_wid = await db.seed_workflow(member)
    bystander = await db.seed_execution(bystander_wid, member, status="waiting_approval")
    before_bystander = db.snapshot(await db.must(bystander))
    paused_event_count = len(await db.events(eid))

    r = await exec_api.client.post(
        f"/executions/{eid}/resume",
        json={"decision": decision, "note": "looks fine"},
        headers=member.headers,
    )
    assert r.status_code == 200, r.text
    assert set(jbody(r)) == EXECUTION_KEYS and jbody(r)["id"] == eid

    row = await db.must(eid)
    assert row.status == "completed", (row.status, row.error)
    assert row.variables["result"] == decision
    assert row.completed_at is not None and row.error is None
    assert row.lease_owner is None and row.lease_expires_at is None and row.running_since is None
    assert row.delivery_attempts == 2  # the run claim and the resume claim
    assert await db.approval_count(eid) == 1
    seqs = await db.event_seqs(eid)
    assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) > paused_event_count
    types = await db.event_types(eid)
    assert types[paused_event_count] == "workflow_started" and types[-1] == "workflow_completed"
    assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_resume_row_state_is_what_the_claim_needs(
    exec_api: ExecApi, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the enqueue replaced, the route leaves a running, lease-free row carrying the decision
    in ``variables``; the claim then picks it up with the real executor."""
    _wid, eid, _emails = await _paused(exec_api, db, member, monkeypatch)
    paused = await db.must(eid)
    rec = install_enqueue(monkeypatch, EnqueueRecorder())

    r = await exec_api.client.post(
        f"/executions/{eid}/resume", json={"decision": "approved"}, headers=member.headers
    )
    assert r.status_code == 200, r.text
    assert rec.calls == [(eid, "resume")]
    row = await db.must(eid)
    assert row.status == "running"
    assert row.lease_owner is None and row.lease_expires_at is None
    assert row.running_since is not None and row.completed_at is None
    assert row.variables["_resume_decision"] == "approved"
    assert {k: v for k, v in row.variables.items() if k != "_resume_decision"} == paused.variables
    assert await db.approval_count(eid) == 1

    claimed = await exec_api.claim(eid, "resume")
    assert claimed.status_code == 200 and jbody(claimed) == {"status": "completed"}
    done = await db.must(eid)
    assert done.status == "completed" and done.variables["result"] == "approved"
    # a second delivery of the same task finds nothing to claim
    again = await exec_api.claim(eid, "resume")
    assert jbody(again) == {"status": "already_claimed"}


async def test_emailed_link_confirm_then_claim_runs_to_completion(
    client: AsyncClient,
    exec_api: ExecApi,
    member: Member,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import get_settings
    from src.security.jwt import create_approval_email_token

    _wid, eid, emails = await _paused(exec_api, db, member, monkeypatch)
    assert len(emails) == 1
    pending_since = str(emails[0]["pending_since"])
    token = create_approval_email_token(
        eid, "ua", "approved", "approver@example.com", pending_since
    )

    r = await client.post(f"/approvals/email/{token}/confirm")
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"{get_settings().frontend_url}/approval-result?status=approved"

    row = await db.must(eid)
    assert row.status == "completed", (row.status, row.error)
    assert row.variables["result"] == "approved" and row.delivery_attempts == 2
    assert await db.approval_count(eid) == 1
    async with db.sm() as s:
        from sqlalchemy import select

        from src.db.models import Approval

        approval = await s.scalar(select(Approval).where(Approval.execution_id == eid))
    assert approval is not None
    assert approval.via_email_link is True and approval.approver_user_id is None
    assert approval.approver_email == "approver@example.com" and approval.node_id == "ua"
    assert approval.decision == "approved"

    # single use: the same link no longer resolves anything
    again = await client.post(f"/approvals/email/{token}/confirm")
    assert again.status_code == 303
    assert again.headers["location"].endswith("/approval-result?status=invalid")
    assert await db.approval_count(eid) == 1
    assert (await db.must(eid)).status == "completed"
