"""Pins GET /approvals/email/{token} and POST /approvals/email/{token}/confirm as they behave today.

Approval tokens are bearer credentials: assertions here compare redirect locations, never print a
token. Tests named ``..._today_...`` pin behaviour that the plan's decisions DB2 and DB6 change in
Task 3; Task 3 may change exactly those.
"""

import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta

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
    checked: list[str] = []

    async def _committed_before_enqueue(execution_id: str) -> None:
        # a separate session: everything the route did is already committed when enqueue runs
        row = await snap(db, execution_id)
        assert row["status"] == "running"
        assert row["variables"]["_resume_decision"] == decision
        assert len(await approvals_of(db, execution_id)) == 1
        checked.append(execution_id)

    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder(before=_committed_before_enqueue))
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
    assert checked == [eid]
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
    approvals = await approvals_of(db, eid)
    assert after["pending_attempt_approval_id"] == approvals[0]["id"]
    same_except(
        before,
        after,
        "status",
        "running_since",
        "lease_owner",
        "lease_expires_at",
        "variables",
        "pending_attempt_approval_id",
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


def _is_client_request_line(record: logging.LogRecord) -> bool:
    """The TEST client's own ``HTTP Request: GET <url>`` line (httpx), which is not Composer output."""
    return record.name == "httpx" and record.getMessage().startswith("HTTP Request:")


def _leaks(record: logging.LogRecord, secrets_: list[str]) -> bool:
    haystack = [
        record.getMessage(),
        str(record.args),
        record.exc_text or "",
        *(str(v) for v in record.__dict__.values()),
    ]
    return any(secret in h for secret in secrets_ for h in haystack)


async def test_token_never_logged(
    client: AsyncClient,
    db: Db,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """DB3 (kept as-is): no Composer log record (nor stdout/stderr) contains an approval token or
    its signature, on the happy path, on invalid links, on an enqueue failure after the resume and
    on a malformed-decision confirm that errors."""
    eid, since = await seed_paused(db, member)
    token = email_token(eid, "approved", since)
    # a second pause whose enqueue fails, a third whose token carries an unknown decision
    eid_fail, since_fail = await seed_paused(db, member)
    token_fail = email_token(eid_fail, "approved", since_fail)
    eid_bad, since_bad = await seed_paused(db, member)
    token_bad = email_token(eid_bad, "maybe", since_bad)
    garbage = "garbage.token.value"
    secrets_ = [t for tok in (token, token_fail, token_bad) for t in (tok, tok.rsplit(".", 1)[1])]
    caplog.set_level(logging.DEBUG)
    # Positive control that does not depend on which ORM the routes use: a Composer logger's
    # DEBUG record must reach caplog, or the leak check below would prove nothing.
    logging.getLogger("src.api.approval_email").debug("token-log-canary")

    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    await open_link(client, token)
    await confirm(client, token)
    await confirm(client, token)
    await open_link(client, garbage)

    rec.raises = RuntimeError("enqueue boom")
    with pytest.raises(RuntimeError, match="enqueue boom"):
        await confirm(client, token_fail)
    # Today the malformed decision errors (DB6); after phase 4b it redirects to `invalid`.
    # Either way, nothing it logs may carry the token.
    with contextlib.suppress(Exception):
        await confirm(client, token_bad)

    assert any(
        r.name == "src.api.approval_email" and r.getMessage() == "token-log-canary"
        for r in caplog.records
    ), "positive control: Composer DEBUG records must reach caplog"
    inspected = [r for r in caplog.records if not _is_client_request_line(r)]
    assert not any(_leaks(r, secrets_) for r in inspected), "an approval token appeared in a log"
    out, err = capfd.readouterr()
    assert not any(secret in out or secret in err for secret in secrets_)


async def test_confirm_with_non_default_node_id(
    client: AsyncClient, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_resume_enqueue(monkeypatch, EnqueueRecorder())
    eid, since = await seed_paused(db, member, node_id="approve-7")
    token = email_token(eid, "rejected", since, node_id="approve-7")
    resp = await confirm(client, token)
    assert location(resp) == result_location("rejected")
    rows = await approvals_of(db, eid)
    assert [(r["node_id"], r["decision"].value) for r in rows] == [("approve-7", "rejected")]
    assert (await snap(db, eid))["variables"] == {
        **paused_variables(since, "approve-7"),
        "_resume_decision": "rejected",
    }
    assert rec.calls == [(eid, "resume")]
