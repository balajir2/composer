"""Race tests for the deliberate changes DB1, DB2 and DB6 (plan decisions), on the oracle's
``hold_row`` lock seam.

These started as pins of the pre-port behaviour and were flipped by phase 4b Task 3; they are the
one oracle file Task 3 may edit. The pre-port ``test_resume_stamp_lands_on_canceled_row_today_db1``
was deleted: after the port the transition, approval insert and stamp are one transaction, so
there is no window to cancel in (the rollback tests in ``test_deliberate_changes.py`` and the repo
claim tests replace it).

The lock-based tests use ``hold_row``: the request reads the row, parks on its guarded UPDATE
behind a separate session's row lock, the holder changes ``variables`` and commits, the request
carries on.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.sqla_phase4a.conftest import HTTPX_MARK, Db, EnqueueRecorder, Member
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
    seed_paused,
    snap,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _set_variables(
    holder: AsyncSession, execution_id: str, variables: dict[str, Any]
) -> None:
    await holder.execute(
        text("UPDATE workflow_executions SET variables = CAST(:v AS jsonb) WHERE id = :e"),
        {"v": json.dumps(variables), "e": execution_id},
    )


async def test_resume_stamp_merges_concurrent_variables(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB1: the stamp is a JSONB merge against the CURRENT value inside the guarded UPDATE, so a
    concurrent write to ``variables`` survives."""
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
    assert row["variables"] == {
        **paused_variables(since),
        "late": "write",
        "_resume_decision": "approved",
    }


async def test_confirm_stale_instance_is_invalid(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB2: the link validates against the pause it was minted for, then the row re-pauses (new
    ``_pending_approval_since``) before the guarded transition. The guard re-checks the pause
    instance, so the stale link is ``invalid`` and the new pause is untouched."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    token = email_token(eid, "approved", since)
    new_since = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()

    async def _repause(holder: AsyncSession) -> None:
        await _set_variables(holder, eid, paused_variables(new_since))

    (resp,) = await race_behind_lock(db, eid, [confirm(client, token)], holder_action=_repause)
    assert resp.status_code == 303
    assert location(resp) == result_location("invalid")
    assert rec.calls == []
    row = await snap(db, eid)
    assert row["status"] == "waiting_approval"
    assert row["variables"] == paused_variables(new_since)
    assert await approvals_of(db, eid) == []


async def test_confirm_malformed_decision_claim_is_invalid(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB6: a validly signed token with an unknown decision is an ``invalid`` redirect before any
    write: no error, row untouched, no approval, no enqueue."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    before = await snap(db, eid)
    token = email_token(eid, "maybe", since)

    resp = await confirm(client, token)

    assert resp.status_code == 303
    assert location(resp) == result_location("invalid")
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []
