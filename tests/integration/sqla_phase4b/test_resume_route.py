"""Pins POST /executions/{id}/resume as it behaves today (post-4a, Prisma)."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.storage.db import get_db
from tests.integration.sqla_phase4a.conftest import (
    EXECUTION_KEYS,
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    ExecApi,
    Member,
    Phase4aAdapter,
    ago,
    install_fake_executor,
    jbody,
    not_found,
)
from tests.integration.sqla_phase4b.conftest import (
    approvals_of,
    install_resume_enqueue,
    not_waiting,
    paused_variables,
    resume,
    same_except,
    seed_paused,
    snap,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

TOLERANCE = timedelta(milliseconds=5)


@pytest.mark.parametrize("decision", ["approved", "rejected"])
@pytest.mark.parametrize("note", ["looks fine", None])
async def test_resume_approved_and_rejected_success(
    client: AsyncClient,
    db: Db,
    member: Member,
    exec_api: ExecApi,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    note: str | None,
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(
        db, member, lease_owner="old-worker", lease_expires_at=ago(minutes=1)
    )
    bystander, _ = await seed_paused(db, member)
    by_before = await snap(db, bystander)
    before = await snap(db, eid)
    events_before = await db.event_count([eid])
    got_before = jbody(await exec_api.get(member, eid))

    t0 = datetime.now(UTC)
    resp = await resume(client, member, eid, decision, note)
    t1 = datetime.now(UTC)

    assert resp.status_code == 200, resp.text
    body = jbody(resp)
    assert set(body) == EXECUTION_KEYS
    assert body["status"] == "running"
    assert body["variables"] == paused_variables(since)  # DB5: no _resume_decision
    assert body["completedAt"] is None
    assert {k: v for k, v in body.items() if k != "status"} == {
        k: v for k, v in got_before.items() if k != "status"
    }
    assert rec.calls == [(eid, "resume")]

    after = await snap(db, eid)
    assert after["status"] == "running"
    assert after["running_since"] is not None
    assert t0 - TOLERANCE <= after["running_since"] <= t1 + TOLERANCE
    assert after["lease_owner"] is None
    assert after["lease_expires_at"] is None
    assert after["completed_at"] is None
    assert after["error"] is None
    assert after["failure_code"] is None
    assert after["delivery_attempts"] == 1
    assert after["variables"] == {**paused_variables(since), "_resume_decision": decision}
    same_except(
        before, after, "status", "running_since", "lease_owner", "lease_expires_at", "variables"
    )

    rows = await approvals_of(db, eid)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row["id"], str) and row["id"]
    created = row.pop("created_at")
    assert t0 - TOLERANCE <= created <= datetime.now(UTC) + TOLERANCE
    row.pop("id")
    decision_value = row.pop("decision").value
    assert decision_value == decision
    assert row == {
        "execution_id": eid,
        "node_id": "ua",
        "approver_user_id": member.id,
        "approver_email": None,
        "via_email_link": False,
        "note": note,
    }
    assert await db.event_count([eid]) == events_before
    assert await snap(db, bystander) == by_before
    assert await approvals_of(db, bystander) == []


async def test_resume_404s(
    client: AsyncClient,
    db: Db,
    member: Member,
    second_member: Member,
    admin: Member,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    resp = await resume(client, member, "no-such-execution", "approved")
    assert resp.status_code == 404
    assert jbody(resp) == {"detail": not_found("no-such-execution")}

    eid, _ = await seed_paused(db, member)
    before = await snap(db, eid)
    resp = await resume(client, second_member, eid, "approved")
    assert resp.status_code == 404
    assert jbody(resp) == {"detail": not_found(eid)}
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []

    resp = await resume(client, admin, eid, "rejected")
    assert resp.status_code == 200, resp.text
    rows = await approvals_of(db, eid)
    assert len(rows) == 1
    assert rows[0]["approver_user_id"] == admin.id
    assert rows[0]["decision"].value == "rejected"
    assert rec.calls == [(eid, "resume")]


@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed", "canceled"])
async def test_resume_409_for_every_other_status(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    eid = await db.seed_execution(
        wid, member, status=status, variables=paused_variables("2026-01-01T00:00:00+00:00")
    )
    before = await snap(db, eid)
    resp = await resume(client, member, eid, "approved")
    assert resp.status_code == 409
    assert jbody(resp) == {"detail": not_waiting(status)}
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_resume_500_without_pending_node(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, _ = await seed_paused(db, member, variables={"k": 1})
    before = await snap(db, eid)
    resp = await resume(client, member, eid, "approved")
    assert resp.status_code == 500
    assert jbody(resp) == {
        "detail": "Execution is waiting_approval but has no _pending_approval_node."
    }
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_resume_422_bad_body(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, _ = await seed_paused(db, member)
    before = await snap(db, eid)
    for body in ({"decision": "maybe"}, {}):
        resp = await client.post(f"/executions/{eid}/resume", json=body, headers=member.headers)
        assert resp.status_code == 422
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_resume_rate_limited(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    monkeypatch.setenv("RATE_LIMIT_RESUME_PER_MINUTE", "1")
    get_settings.cache_clear()
    a, _ = await seed_paused(db, member)
    b, _ = await seed_paused(db, member)
    before_b = await snap(db, b)

    assert (await resume(client, member, a, "approved")).status_code == 200
    resp = await resume(client, member, b, "approved")
    assert resp.status_code == 429
    assert jbody(resp) == {"detail": "Rate limit exceeded. Retry after 60 seconds."}
    assert resp.headers["retry-after"] == "60"
    assert await snap(db, b) == before_b
    assert await approvals_of(db, b) == []
    assert rec.calls == [(a, "resume")]


async def test_enqueue_failure_leaves_running_row_for_the_orphan_sweeper(
    client: AsyncClient,
    db: Db,
    member: Member,
    adapter: Phase4aAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB4 (kept as-is): the row is already running when enqueue fails; the orphan sweep fails it."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder(raises=RuntimeError("enqueue boom")))
    eid, since = await seed_paused(db, member)
    with pytest.raises(RuntimeError, match="enqueue boom"):
        await resume(client, member, eid, "approved")
    assert rec.calls == [(eid, "resume")]
    row = await snap(db, eid)
    assert row["status"] == "running"
    assert row["variables"] == {**paused_variables(since), "_resume_decision": "approved"}
    assert row["lease_owner"] is None
    assert len(await approvals_of(db, eid)) == 1

    await adapter.sweep_orphaned(grace_seconds=1, now=datetime.now(UTC) + timedelta(seconds=5))
    row = await snap(db, eid)
    assert row["status"] == "failed"
    assert row["failure_code"] == "orphaned_running"


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_resumed_row_is_claimable_by_a_resume_task(
    client: AsyncClient,
    db: Db,
    member: Member,
    exec_api: ExecApi,
    sessionmaker_: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    log = install_fake_executor(monkeypatch, sessionmaker_)
    eid, _ = await seed_paused(db, member)
    assert (await resume(client, member, eid, decision)).status_code == 200

    first = await exec_api.claim(eid, "resume")
    assert first.status_code == 200
    assert jbody(first) == {"status": "completed"}
    assert log.resumes == [(eid, decision)]
    assert log.seen[0]["status"] == "running"
    assert log.seen[0]["lease_owner"] is not None

    second = await exec_api.claim(eid, "resume")
    assert jbody(second) == {"status": "already_claimed"}


# --------------------------------------------------------------------------- DB1 today pins


class _ApprovalHook:
    """Wraps ``db.approval`` so a hook runs right before the audit row is created (i.e. between the
    guarded transition and the unguarded ``_resume_decision`` stamp)."""

    def __init__(self, real: Any, hook: Callable[[], Awaitable[None]]) -> None:
        self._real = real
        self._hook = hook

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        await self._hook()
        return await self._real.create(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _DbProxy:
    def __init__(self, real: Any, hook: Callable[[], Awaitable[None]]) -> None:
        self._real = real
        self.approval = _ApprovalHook(real.approval, hook)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _hook_between_transition_and_stamp(app: FastAPI, hook: Callable[[], Awaitable[None]]) -> None:
    real = app.state.db
    app.dependency_overrides[get_db] = lambda: _DbProxy(real, hook)


async def test_resume_stamp_lands_on_canceled_row_today_db1(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    member: Member,
    exec_api: ExecApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB1 reproduced: the transition, the approval insert and the stamp are three autocommitted
    statements. A cancel landing between them leaves a canceled row that still gets the resume
    decision stamped, the resume answers 200 and a resume task is enqueued for it. After Task 3
    (stamp inside the guarded UPDATE, approval in the same transaction) the stamp can no longer
    land after a cancel; Task 3 may change this test."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)

    async def _cancel() -> None:
        assert (await exec_api.cancel(member, eid)).status_code == 200

    _hook_between_transition_and_stamp(app, _cancel)
    try:
        resp = await resume(client, member, eid, "approved")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert resp.status_code == 200, resp.text
    row = await snap(db, eid)
    assert row["status"] == "canceled"
    assert row["error"] == "Execution canceled by user request."
    assert row["variables"] == {**paused_variables(since), "_resume_decision": "approved"}
    assert len(await approvals_of(db, eid)) == 1
    assert rec.calls == [(eid, "resume")]


async def test_resume_stamp_overwrites_concurrent_variables_today_db1(
    app: FastAPI,
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB1 reproduced: the stamp rewrites ``variables`` from the snapshot read BEFORE the
    transition, so a concurrent change to ``variables`` is lost. After Task 3 the stamp is a JSONB
    merge against the current value; Task 3 flips this test."""
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)

    async def _write_late() -> None:
        await db.set_execution(eid, variables={**paused_variables(since), "late": "write"})

    _hook_between_transition_and_stamp(app, _write_late)
    try:
        resp = await resume(client, member, eid, "approved")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert resp.status_code == 200, resp.text
    row = await snap(db, eid)
    assert row["variables"] == {**paused_variables(since), "_resume_decision": "approved"}
    assert "late" not in row["variables"]
