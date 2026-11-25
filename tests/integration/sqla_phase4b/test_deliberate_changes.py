"""Regression tests for the deliberate changes DB1 and DB2 (phase 4b plan decisions).

Not part of the behaviour oracle: these pin behaviour the pre-port code did NOT have and fail on
it. (DB1's stamp-merge and DB2/DB6 race tests live in ``test_today_pins.py``, flipped from their
pre-port pins.)

* DB1: resume is one transaction. A failing approval insert rolls the transition back (the row
  stays ``waiting_approval`` with its original variables) and nothing is enqueued.
* DB2: a confirm whose token was minted for an earlier pause instance cannot resolve a later
  pause, even when the re-pause lands between the route's read and its guarded UPDATE.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from src.db.repos import executions as executions_repo
from tests.integration.sqla_phase4a.conftest import HTTPX_MARK, Db, EnqueueRecorder, Member
from tests.integration.sqla_phase4b.conftest import (
    approvals_of,
    confirm,
    email_token,
    install_resume_enqueue,
    location,
    paused_variables,
    result_location,
    resume,
    seed_paused,
    snap,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _boom(*_a: Any, **_k: Any) -> None:
    raise RuntimeError("insert failed")


async def test_resume_rolls_back_when_the_approval_insert_fails(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, _since = await seed_paused(db, member)
    before = await snap(db, eid)
    monkeypatch.setattr("src.api.executions.approvals_repo.create", _boom)

    with pytest.raises(RuntimeError, match="insert failed"):
        await resume(client, member, eid, "approved")

    assert await snap(db, eid) == before  # status, variables and lease columns unchanged
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_confirm_rolls_back_when_the_approval_insert_fails(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    before = await snap(db, eid)
    monkeypatch.setattr("src.api.approval_email.approvals_repo.create", _boom)

    with pytest.raises(RuntimeError, match="insert failed"):
        await confirm(client, email_token(eid, "approved", since))

    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_confirm_with_a_stale_instance_after_repause_is_invalid(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row re-pauses (new ``_pending_approval_since``) after the route's read validated the
    old instance but before its guarded UPDATE. Without DB2 the old token resumes the new pause."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    token = email_token(eid, "approved", since)
    new_since = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    real_get = executions_repo.get
    repaused = False

    async def _get_then_repause(session: Any, execution_id: str) -> Any:
        nonlocal repaused
        record = await real_get(session, execution_id)
        if not repaused:
            repaused = True
            await db.set_execution(eid, variables=paused_variables(new_since))
        return record  # the OLD view: the route validated against the previous pause

    monkeypatch.setattr(executions_repo, "get", _get_then_repause)

    resp = await confirm(client, token)

    assert resp.status_code == 303
    assert location(resp) == result_location("invalid")
    row = await snap(db, eid)
    assert row["status"] == "waiting_approval"
    assert row["variables"] == paused_variables(new_since)  # no _resume_decision
    assert await approvals_of(db, eid) == []
    assert rec.calls == []
