"""Pins of TODAY's buggy behaviour (plan decisions DB1, DB2, DB6). Task 3 may change THIS FILE ONLY.

Everything else in this package must pass unchanged after the routes move to SQLAlchemy. Here:

* ``test_resume_stamp_lands_on_canceled_row_today_db1``: after the port the three autocommitted
  statements are one transaction, so there is no window to cancel in. Task 3 deletes this test
  (the rollback and repo claim tests replace it).
* ``test_resume_stamp_overwrites_concurrent_variables_today_db1`` and
  ``test_confirm_stale_instance_resolves_new_pause_today_db2``: flipped in Task 3 (same mechanics,
  the assertions change; see the comments in each).
* ``test_confirm_malformed_decision_claim_500s_today_db6``: flipped in Task 3 to ``invalid``.

The two lock-based pins use ``hold_row``: the request reads the row, parks on its guarded UPDATE
behind a separate session's row lock, the holder changes ``variables`` and commits, the request
carries on.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.db import get_db
from tests.integration.sqla_phase4a.conftest import HTTPX_MARK, Db, EnqueueRecorder, ExecApi, Member
from tests.integration.sqla_phase4b.conftest import (
    approvals_of,
    confirm,
    email_token,
    install_resume_enqueue,
    location,
    paused_variables,
    race_behind_lock,
    result_location,
    resume,
    same_except,
    seed_paused,
    snap,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


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


async def _set_variables(
    holder: AsyncSession, execution_id: str, variables: dict[str, Any]
) -> None:
    await holder.execute(
        text("UPDATE workflow_executions SET variables = CAST(:v AS jsonb) WHERE id = :e"),
        {"v": json.dumps(variables), "e": execution_id},
    )


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
    decision stamped, the resume answers 200 and a resume task is enqueued for it. After the port
    there is no such window; Task 3 DELETES this test."""
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
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB1 reproduced: the stamp rewrites ``variables`` from the snapshot read BEFORE the
    transition, so a concurrent write to ``variables`` is lost. Task 3 flips the last assertion:
    the stamp is a JSONB merge against the current value, so ``late`` survives (the plan's
    ``test_resume_stamp_merges_concurrent_variables``)."""
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)

    async def _write_late(holder: AsyncSession) -> None:
        await _set_variables(holder, eid, {**paused_variables(since), "late": "write"})

    (resp,) = await race_behind_lock(
        db, eid, [resume(client, member, eid, "approved")], holder_action=_write_late
    )
    assert resp.status_code == 200, resp.text
    row = await snap(db, eid)
    assert row["status"] == "running"
    assert row["variables"]["_resume_decision"] == "approved"
    assert (
        "late" not in row["variables"]
    )  # TODAY: lost. After Task 3: row["variables"]["late"] == "write"


async def test_confirm_stale_instance_resolves_new_pause_today_db2(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB2 reproduced: the link validates against the pause it was minted for, then the row
    re-pauses (new ``_pending_approval_since``) before the guarded transition. Today the
    status-only guard lets the stale link resolve the NEW pause, and the stamp restores the OLD
    ``since`` from the pre-re-pause snapshot. Task 3 flips this to: ``invalid`` redirect, no
    approval, no enqueue, row still ``waiting_approval`` with the new pause markers."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    token = email_token(eid, "approved", since)
    new_since = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()

    async def _repause(holder: AsyncSession) -> None:
        await _set_variables(holder, eid, paused_variables(new_since))

    (resp,) = await race_behind_lock(db, eid, [confirm(client, token)], holder_action=_repause)
    assert resp.status_code == 303
    assert location(resp) == result_location("approved")  # TODAY: the stale link wins
    assert rec.calls == [(eid, "resume")]
    row = await snap(db, eid)
    assert row["status"] == "running"
    assert row["variables"] == {**paused_variables(since), "_resume_decision": "approved"}
    assert len(await approvals_of(db, eid)) == 1


async def test_confirm_malformed_decision_claim_500s_today_db6(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB6 reproduced: a validly signed token with an unknown decision passes the guard, then the
    approval insert fails: the request errors and the row is stuck ``running`` with no approval row
    and no enqueue. Task 3 flips this to an ``invalid`` redirect before any write (row untouched)."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    before = await snap(db, eid)
    token = email_token(eid, "maybe", since)

    with pytest.raises(Exception):  # noqa: B017 - the exact class is the Prisma enum error
        await confirm(client, token)

    after = await snap(db, eid)
    assert after["status"] == "running"
    same_except(before, after, "status", "running_since", "lease_owner", "lease_expires_at")
    assert await approvals_of(db, eid) == []
    assert rec.calls == []
