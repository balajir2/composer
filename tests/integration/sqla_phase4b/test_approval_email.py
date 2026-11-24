"""Pins GET /approvals/email/{token} and POST /approvals/email/{token}/confirm as they behave today.

Approval tokens are bearer credentials: assertions here compare redirect locations, never print a
token. Tests named ``..._today_...`` pin behaviour that the plan's decisions DB2 and DB6 change in
Task 3; Task 3 may change exactly those.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from jose import jwt as jose_jwt

from src.config import get_settings
from src.security.jwt import ApprovalEmailTokenPayload
from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    Member,
    ago,
    jbody,
)
from tests.integration.sqla_phase4b.conftest import (
    APPROVER,
    approvals_of,
    confirm,
    confirm_location,
    email_token,
    install_resume_enqueue,
    location,
    open_link,
    paused_variables,
    result_location,
    same_except,
    seed_paused,
    snap,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

TOLERANCE = timedelta(milliseconds=5)

INVALID_CASES = [
    "garbage",
    "access_token",
    "wrong_secret",
    "expired",
    "missing_execution",
    "node_mismatch",
    "stale_pending_since",
    "status_queued",
    "status_running",
    "status_completed",
    "status_failed",
    "status_canceled",
]


async def build_invalid_case(
    case: str, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """Returns (execution_id, token); the execution is a real row (or absent for
    missing_execution) and the token is invalid for the given reason."""
    status = case.removeprefix("status_") if case.startswith("status_") else "waiting_approval"
    since = datetime.now(UTC).isoformat()
    wid = await db.seed_workflow(member)
    eid = await db.seed_execution(
        wid, member, status=status, variables=paused_variables(since), delivery_attempts=1
    )
    if case == "garbage":
        return eid, "not-a-token"
    if case == "access_token":
        return eid, member.token
    if case == "wrong_secret":
        now = int(time.time())
        claims = ApprovalEmailTokenPayload(
            sub=eid,
            node_id="ua",
            decision="approved",
            approver_email=APPROVER,
            pending_since=since,
            iat=now,
            exp=now + 3600,
        )
        return eid, jose_jwt.encode(claims.model_dump(), "other", algorithm="HS256")
    if case == "expired":
        ttl = get_settings().approval_link_ttl_hours
        with monkeypatch.context() as m:
            m.setattr("src.security.jwt._now", lambda: int(time.time()) - ttl * 3600 - 10)
            return eid, email_token(eid, "approved", since)
    if case == "missing_execution":
        return eid, email_token("no-such-execution", "approved", since)
    if case == "node_mismatch":
        return eid, email_token(eid, "approved", since, node_id="other")
    if case == "stale_pending_since":
        older = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        return eid, email_token(eid, "approved", older)
    return eid, email_token(eid, "approved", since)


# --------------------------------------------------------------------------- GET


async def test_get_valid_token_redirects_to_confirm_and_never_mutates(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    before = await snap(db, eid)
    token = email_token(eid, "approved", since)
    for _ in range(3):
        resp = await open_link(client, token)
        assert resp.status_code == 303
        assert location(resp) == confirm_location(token, "approved")
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


@pytest.mark.parametrize("case", INVALID_CASES)
async def test_get_invalid_cases(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, token = await build_invalid_case(case, db, member, monkeypatch)
    before = await snap(db, eid)
    resp = await open_link(client, token)
    assert resp.status_code == 303
    assert location(resp) == result_location("invalid")
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


# --------------------------------------------------------------------------- POST /confirm


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_confirm_success(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(
        db, member, lease_owner="old-worker", lease_expires_at=ago(minutes=1)
    )
    bystander, _ = await seed_paused(db, member)
    by_before = await snap(db, bystander)
    before = await snap(db, eid)
    events_before = await db.event_count([eid])
    token = email_token(eid, decision, since)

    t0 = datetime.now(UTC)
    resp = await confirm(client, token)
    t1 = datetime.now(UTC)

    assert resp.status_code == 303
    assert location(resp) == result_location(decision)
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
    created = row.pop("created_at")
    assert t0 - TOLERANCE <= created <= datetime.now(UTC) + TOLERANCE
    assert isinstance(row.pop("id"), str)
    assert row.pop("decision").value == decision
    assert row == {
        "execution_id": eid,
        "node_id": "ua",
        "approver_user_id": None,
        "approver_email": APPROVER,
        "via_email_link": True,
        "note": None,
    }
    assert await db.event_count([eid]) == events_before
    assert await snap(db, bystander) == by_before
    assert await approvals_of(db, bystander) == []


@pytest.mark.parametrize("case", INVALID_CASES)
async def test_confirm_invalid_cases(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, token = await build_invalid_case(case, db, member, monkeypatch)
    before = await snap(db, eid)
    resp = await confirm(client, token)
    assert resp.status_code == 303
    assert location(resp) == result_location("invalid")
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_confirm_is_single_use(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    approve = email_token(eid, "approved", since)
    reject = email_token(eid, "rejected", since)

    assert location(await confirm(client, approve)) == result_location("approved")
    after = await snap(db, eid)
    assert location(await confirm(client, approve)) == result_location("invalid")
    assert location(await confirm(client, reject)) == result_location("invalid")
    assert location(await open_link(client, approve)) == result_location("invalid")
    assert location(await open_link(client, reject)) == result_location("invalid")
    assert await snap(db, eid) == after
    assert len(await approvals_of(db, eid)) == 1
    assert rec.calls == [(eid, "resume")]


async def test_rate_limit_shared_by_get_and_confirm(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    monkeypatch.setenv("RATE_LIMIT_APPROVAL_EMAIL_PER_MINUTE", "2")
    get_settings.cache_clear()
    eid, since = await seed_paused(db, member)
    before = await snap(db, eid)
    token = email_token(eid, "approved", since)

    assert (await open_link(client, token)).status_code == 303
    assert (await open_link(client, token)).status_code == 303
    resp = await confirm(client, token)
    assert resp.status_code == 429
    assert jbody(resp) == {"detail": "Rate limit exceeded. Retry after 30 seconds."}
    assert resp.headers["retry-after"] == "30"
    assert await snap(db, eid) == before
    assert await approvals_of(db, eid) == []
    assert rec.calls == []


async def test_confirm_enqueue_failure(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB4 (kept as-is), email side: enqueue failure leaves the row running with its decision."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder(raises=RuntimeError("enqueue boom")))
    eid, since = await seed_paused(db, member)
    token = email_token(eid, "approved", since)
    with pytest.raises(RuntimeError, match="enqueue boom"):
        await confirm(client, token)
    assert rec.calls == [(eid, "resume")]
    row = await snap(db, eid)
    assert row["status"] == "running"
    assert row["variables"] == {**paused_variables(since), "_resume_decision": "approved"}
    assert len(await approvals_of(db, eid)) == 1


async def test_token_never_logged(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DB3 (kept as-is): no Composer log record contains the token or its signature."""
    install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    token = email_token(eid, "approved", since)
    signature = token.rsplit(".", 1)[1]
    garbage = "garbage.token.value"
    caplog.set_level(logging.DEBUG)

    await open_link(client, token)
    await confirm(client, token)
    await confirm(client, token)
    await open_link(client, garbage)

    assert caplog.records, "expected some log output to inspect"
    leaked = False
    for record in caplog.records:
        if record.name.split(".")[0] in {"httpx", "httpcore"}:
            continue  # the TEST client's own request line (HTTP Request: GET <url>), not Composer
        haystack = [
            record.getMessage(),
            str(record.args),
            record.exc_text or "",
            *(str(v) for v in record.__dict__.values()),
        ]
        if any(token in h or signature in h for h in haystack):
            leaked = True
    assert not leaked, "an approval token appeared in a log record"


# --------------------------------------------------------------------------- today-only pins


async def test_confirm_stale_instance_resolves_new_pause_today_db2(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB2 reproduced: validation passes, then the row re-pauses (new pending_since) before the
    guarded transition. Today the status-only guard lets the stale link resolve the NEW pause.
    After Task 3 (pause instance inside the guard) the confirm must be ``invalid`` and the row
    untouched; Task 3 flips this test."""
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member)
    token = email_token(eid, "approved", since)
    new_since = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()

    import src.api.approval_email as module

    original: Any = module._validate_token_and_execution  # pyright: ignore[reportPrivateUsage]

    async def _validate_then_repause(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        await db.set_execution(eid, variables=paused_variables(new_since))
        return result

    monkeypatch.setattr(module, "_validate_token_and_execution", _validate_then_repause)

    resp = await confirm(client, token)
    assert resp.status_code == 303
    assert location(resp) == result_location("approved")  # TODAY: stale link wins
    assert rec.calls == [(eid, "resume")]
    row = await snap(db, eid)
    assert row["status"] == "running"
    # the unguarded stamp merges onto the PRE-re-pause snapshot, clobbering the new pause markers
    assert row["variables"] == {**paused_variables(since), "_resume_decision": "approved"}
    assert len(await approvals_of(db, eid)) == 1


async def test_confirm_malformed_decision_claim_500s_today_db6(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB6 reproduced: a validly signed token with an unknown decision passes the guard, then the
    approval insert fails: the request errors and the row is stuck ``running`` with no approval row
    and no enqueue. After Task 3 this must be an ``invalid`` redirect before any write; Task 3
    flips this test."""
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
