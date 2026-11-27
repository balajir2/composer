"""A run paused for approval whose workflow was edited into an unrunnable canvas.

Resuming must be refused with the friendly 422 BEFORE a decision is recorded or
a resume is enqueued, so the pause survives: fix the workflow, approve again.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from src.db.models import Workflow
from tests.integration.sqla_phase4a.conftest import HTTPX_MARK, Db, EnqueueRecorder, Member
from tests.integration.sqla_phase4b.conftest import (
    approvals_of,
    confirm,
    email_token,
    install_resume_enqueue,
    paused_variables,
    result_location,
    resume,
    snap,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

_NODES: list[dict[str, Any]] = [
    {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "S"}},
    {
        "id": "ua",
        "type": "user-approval",
        "position": {"x": 1, "y": 0},
        "data": {"label": "Approve", "approvalMessage": "ok?"},
    },
    {"id": "e", "type": "end", "position": {"x": 2, "y": 0}, "data": {"label": "E"}},
]
_E0 = {"id": "e0", "source": "s", "target": "ua"}
_E1 = {"id": "e1", "source": "ua", "target": "e", "branch": "approved"}
_E2 = {"id": "e2", "source": "ua", "target": "e", "branch": "rejected"}


async def _paused_on(db: Db, member: Member, edges: list[dict[str, Any]]) -> tuple[str, str, str]:
    from datetime import UTC, datetime

    since = datetime.now(UTC).isoformat()
    wid = await db.seed_workflow(member, nodes=_NODES, edges=edges)
    eid = await db.seed_execution(
        wid,
        member,
        status="waiting_approval",
        variables=paused_variables(since),
        delivery_attempts=1,
    )
    return wid, eid, since


async def test_api_resume_is_refused_when_the_workflow_became_unrunnable(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    _wid, eid, _since = await _paused_on(db, member, [_E0, _E1])  # `rejected` unconnected
    before = await snap(db, eid)

    resp = await resume(client, member, eid, "approved")

    assert resp.status_code == 422, resp.text
    assert 'has no "rejected" path' in resp.json()["detail"]
    assert await snap(db, eid) == before  # still waiting_approval, nothing resolved
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_api_resume_works_once_the_workflow_is_fixed(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    wid, eid, _since = await _paused_on(db, member, [_E0, _E1])
    refused = await resume(client, member, eid, "approved")
    assert refused.status_code == 422

    fixed = await client.get(f"/workflows/{wid}", headers=member.headers)
    body = {**fixed.json(), "edges": [_E0, _E1, _E2]}
    assert (
        await client.put(f"/workflows/{wid}", json=body, headers=member.headers)
    ).status_code == 200

    ok = await resume(client, member, eid, "approved")
    assert ok.status_code == 200, ok.text
    assert rec.calls == [(eid, "resume")]


async def test_email_resume_is_refused_and_keeps_the_pause(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    _wid, eid, since = await _paused_on(db, member, [_E0, _E1])
    before = await snap(db, eid)
    token = email_token(eid, "approved", since, node_id="ua")

    resp = await confirm(client, token)

    assert resp.status_code == 303
    assert resp.headers["location"] == result_location("unrunnable")
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []

    # the same link works once the workflow is complete again
    fixed = await client.get(f"/workflows/{_wid}", headers=member.headers)
    body = {**fixed.json(), "edges": [_E0, _E1, _E2]}
    assert (
        await client.put(f"/workflows/{_wid}", json=body, headers=member.headers)
    ).status_code == 200
    again = await confirm(client, token)
    assert again.headers["location"] == result_location("approved")
    assert rec.calls == [(eid, "resume")]


# Not a missing branch: `s` fans out to `ua` and `t`, so `e` (three incoming
# edges) is reachable by parallel paths and would run twice.
_FANOUT_NODES: list[dict[str, Any]] = [
    *_NODES,
    {
        "id": "t",
        "type": "set-state",
        "position": {"x": 1, "y": 1},
        "data": {"label": "T", "stateKey": "k", "stateValue": "v"},
    },
]
_FANOUT_EDGES: list[dict[str, Any]] = [
    _E0,
    _E1,
    _E2,
    {"id": "e3", "source": "s", "target": "t"},
    {"id": "e4", "source": "t", "target": "e"},
]
_END_RULE = "must have exactly one incoming edge"


async def _edit_into_end_violation(db: Db, wid: str) -> None:
    async with db.sm() as session:
        await session.execute(
            update(Workflow)
            .where(Workflow.id == wid)
            .values(nodes=_FANOUT_NODES, edges=_FANOUT_EDGES)
        )
        await session.commit()


async def test_api_resume_is_refused_when_the_workflow_now_violates_the_end_rule(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    wid, eid, _since = await _paused_on(db, member, [_E0, _E1, _E2])
    await _edit_into_end_violation(db, wid)
    before = await snap(db, eid)

    resp = await resume(client, member, eid, "approved")

    assert resp.status_code == 422, resp.text
    assert _END_RULE in resp.json()["detail"]
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_email_resume_is_refused_when_the_workflow_now_violates_the_end_rule(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    wid, eid, since = await _paused_on(db, member, [_E0, _E1, _E2])
    await _edit_into_end_violation(db, wid)
    before = await snap(db, eid)

    resp = await confirm(client, email_token(eid, "approved", since, node_id="ua"))

    assert resp.status_code == 303
    assert resp.headers["location"] == result_location("unrunnable")
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_run_is_refused_for_a_stored_shape_violation_with_no_execution_created(
    client: AsyncClient, db: Db, member: Member
) -> None:
    wid = await db.seed_workflow(member, nodes=_FANOUT_NODES, edges=_FANOUT_EDGES)

    resp = await client.post(
        "/executions", json={"workflowId": wid, "input": ""}, headers=member.headers
    )

    assert resp.status_code == 422, resp.text
    assert _END_RULE in resp.json()["detail"]
    assert await db.executions_of(wid) == []


# A run paused on an approval inside one parallel lane, then the canvas is edited
# so the `rejected` path ends at its own End: the Join would wait forever.
def _step(node_id: str, y: int) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "set-state",
        "position": {"x": 1, "y": y},
        "data": {"label": node_id, "stateKey": node_id, "stateValue": "v"},
    }


_JOIN_NODES: list[dict[str, Any]] = [
    *_NODES[:2],
    _step("p", 0),
    _step("ok", 1),
    _step("b", 2),
    {"id": "j", "type": "join", "position": {"x": 2, "y": 1}, "data": {"label": "J"}},
    {"id": "e", "type": "end", "position": {"x": 3, "y": 1}, "data": {"label": "E"}},
    {"id": "e2", "type": "end", "position": {"x": 3, "y": 2}, "data": {"label": "E2"}},
]
_JOIN_COMMON: list[dict[str, Any]] = [
    {"id": "j0", "source": "s", "target": "p"},
    {"id": "j1", "source": "p", "target": "ua"},
    {"id": "j2", "source": "ua", "target": "ok", "branch": "approved"},
    {"id": "j3", "source": "ok", "target": "j"},
    {"id": "j4", "source": "p", "target": "b"},
    {"id": "j5", "source": "b", "target": "j"},
    {"id": "j6", "source": "j", "target": "e"},
]
_JOIN_ESCAPES = [*_JOIN_COMMON, {"id": "j7", "source": "ua", "target": "e2", "branch": "rejected"}]
_JOIN_FIXED = [*_JOIN_COMMON, {"id": "j7", "source": "ua", "target": "j", "branch": "rejected"}]


async def test_api_resume_is_refused_when_a_join_would_wait_forever(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    since = datetime.now(UTC).isoformat()
    wid = await db.seed_workflow(member, nodes=_JOIN_NODES, edges=_JOIN_ESCAPES)
    eid = await db.seed_execution(
        wid,
        member,
        status="waiting_approval",
        variables=paused_variables(since),
        delivery_attempts=1,
    )
    before = await snap(db, eid)

    resp = await resume(client, member, eid, "approved")

    assert resp.status_code == 422, resp.text
    assert "waits for every parallel path" in resp.json()["detail"]
    assert await snap(db, eid) == before  # the pause is kept
    assert await approvals_of(db, eid) == []
    assert rec.calls == []

    fixed = await client.get(f"/workflows/{wid}", headers=member.headers)
    nodes = [n for n in fixed.json()["nodes"] if n["id"] != "e2"]  # its own End is gone
    body = {**fixed.json(), "nodes": nodes, "edges": _JOIN_FIXED}
    assert (
        await client.put(f"/workflows/{wid}", json=body, headers=member.headers)
    ).status_code == 200
    ok = await resume(client, member, eid, "approved")
    assert ok.status_code == 200, ok.text
    assert rec.calls == [(eid, "resume")]
