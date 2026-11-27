"""A run paused for approval whose workflow was edited into an unrunnable canvas.

Resuming must be refused with the friendly 422 BEFORE a decision is recorded or
a resume is enqueued, so the pause survives: fix the workflow, approve again.
"""

from typing import Any

import pytest
from httpx import AsyncClient

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
