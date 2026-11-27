"""Group 6: POST /internal/claim-and-run with a fake executor (the claim, not the graph)."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    ExecApi,
    Member,
    Phase4aAdapter,
    ago,
    install_fake_executor,
    jbody,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

ALREADY = {"status": "already_claimed"}
WORKER_ID = re.compile(r"^.+:\d+$")


def _lease_seconds() -> int:
    from src.config import get_settings

    return get_settings().execution_lease_seconds


async def _seed(db: Db, wid: str, owner: Member, status: str, **cols: Any) -> str:
    return await db.seed_execution(
        wid, owner, status=status, variables=cols.pop("variables", {"v": 1}), **cols
    )


async def test_run_claim_hands_over_committed_lease(
    exec_api: ExecApi,
    member: Member,
    db: Db,
    sessionmaker_: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "queued", delivery_attempts=2)
    bystander = await _seed(db, wid, member, "queued")
    before = db.snapshot(await db.must(eid))
    before_bystander = db.snapshot(await db.must(bystander))
    t0 = datetime.now(UTC)

    r = await exec_api.claim(eid, "run")
    assert r.status_code == 200 and jbody(r) == {"status": "completed"}
    assert log.runs == [eid] and log.resumes == []

    # What the fake saw through its OWN session: the claim had already committed.
    (seen,) = log.seen
    assert seen["status"] == "running"
    assert WORKER_ID.match(seen["lease_owner"]), seen["lease_owner"]
    lease = _lease_seconds()
    assert abs((seen["lease_expires_at"] - (t0 + timedelta(seconds=lease))).total_seconds()) < 5
    assert abs((seen["running_since"] - t0).total_seconds()) < 5
    assert seen["delivery_attempts"] == 3
    assert seen["variables"] == {"v": 1}

    after = db.snapshot(await db.must(eid))
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {
        "status",
        "lease_owner",
        "lease_expires_at",
        "running_since",
        "delivery_attempts",
    }
    assert after == seen  # the fake finished nothing: the row is as claimed
    assert db.snapshot(await db.must(bystander)) == before_bystander


RUN_CASES: list[tuple[str, dict[str, Any], bool]] = [
    # (status, extra columns, claimable)
    ("queued", {}, True),
    ("queued", {"lease_owner": "w"}, False),
    ("queued", {"lease_expires_at": "future"}, False),
    ("running", {}, False),
    ("waiting_approval", {}, False),
    ("completed", {}, False),
    ("failed", {}, False),
    ("canceled", {}, False),
]
RESUME_CASES: list[tuple[str, dict[str, Any], bool]] = [
    ("running", {}, True),
    ("running", {"lease_owner": "w"}, False),
    ("running", {"lease_expires_at": "future"}, False),
    ("queued", {}, False),
    ("waiting_approval", {}, False),
    ("completed", {}, False),
    ("failed", {}, False),
    ("canceled", {}, False),
]


def _cols(extra: dict[str, Any]) -> dict[str, Any]:
    return {k: (ago(minutes=-20) if v == "future" else v) for k, v in extra.items()}


@pytest.mark.parametrize(("kind", "cases"), [("run", RUN_CASES), ("resume", RESUME_CASES)])
async def test_claim_eligibility_matrix(
    exec_api: ExecApi,
    member: Member,
    db: Db,
    sessionmaker_: Any,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    cases: list[tuple[str, dict[str, Any], bool]],
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)
    wid = await db.seed_workflow(member)
    for status, extra, claimable in cases:
        eid = await _seed(
            db, wid, member, status, variables={"_resume_decision": "approved"}, **_cols(extra)
        )
        bystander = await _seed(db, wid, member, "queued")
        before = db.snapshot(await db.must(eid))
        before_bystander = db.snapshot(await db.must(bystander))
        runs_before = len(log.runs) + len(log.resumes)

        r = await exec_api.claim(eid, kind)
        label = f"{kind}/{status}/{extra}"
        if claimable:
            assert r.status_code == 200 and jbody(r) == {"status": "completed"}, label
            assert len(log.runs) + len(log.resumes) == runs_before + 1, label
            after = await db.must(eid)
            assert after.status == "running" and after.delivery_attempts == 1, label
        else:
            assert r.status_code == 200 and jbody(r) == ALREADY, (label, r.text)
            assert len(log.runs) + len(log.resumes) == runs_before, label
            assert db.snapshot(await db.must(eid)) == before, label
        assert db.snapshot(await db.must(bystander)) == before_bystander, label


async def test_claim_unknown_execution_is_already_claimed(
    exec_api: ExecApi, sessionmaker_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)
    r = await exec_api.claim("no-such-execution", "run")
    assert r.status_code == 200 and jbody(r) == ALREADY
    assert log.runs == []


async def test_default_kind_is_run(
    exec_api: ExecApi, member: Member, db: Db, sessionmaker_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "queued")
    r = await exec_api.client.post("/internal/claim-and-run", json={"executionId": eid})
    assert r.status_code == 200 and jbody(r) == {"status": "completed"}
    assert log.runs == [eid]


async def test_five_concurrent_claims_exactly_one_runs(
    exec_api: ExecApi, member: Member, db: Db, sessionmaker_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)
    wid = await db.seed_workflow(member)
    for _ in range(4):
        eid = await _seed(db, wid, member, "queued")
        bystander = await _seed(db, wid, member, "queued")
        before_bystander = db.snapshot(await db.must(bystander))
        runs_before = len(log.runs)
        responses = await asyncio.gather(*(exec_api.claim(eid, "run") for _ in range(6)))
        assert [r.status_code for r in responses] == [200] * 6, [r.text for r in responses]
        bodies = sorted(jbody(r)["status"] for r in responses)
        assert bodies == ["already_claimed"] * 5 + ["completed"], bodies
        assert len(log.runs) == runs_before + 1 and log.runs[-1] == eid
        row = await db.must(eid)
        assert row.delivery_attempts == 1 and row.status == "running"
        assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_resume_passes_decision_from_variables(
    exec_api: ExecApi, member: Member, db: Db, sessionmaker_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)
    wid = await db.seed_workflow(member)
    eid = await _seed(
        db, wid, member, "running", variables={"_resume_decision": "rejected", "x": [1, 2]}
    )
    r = await exec_api.claim(eid, "resume")
    assert r.status_code == 200 and jbody(r) == {"status": "completed"}
    assert log.resumes == [(eid, "rejected")] and log.runs == []
    (seen,) = log.seen
    assert seen["status"] == "running" and WORKER_ID.match(seen["lease_owner"])
    assert seen["delivery_attempts"] == 1
    assert seen["variables"] == {"_resume_decision": "rejected", "x": [1, 2]}


@pytest.mark.parametrize("variables", [{}, {"_resume_decision": ""}, {"_resume_decision": None}])
async def test_resume_without_decision_is_422_and_fails_the_row_fast(
    exec_api: ExecApi,
    member: Member,
    db: Db,
    sessionmaker_: Any,
    monkeypatch: pytest.MonkeyPatch,
    variables: dict[str, Any],
) -> None:
    """The 422 is still returned, but the row no longer waits out its lease: it is failed with
    its attempt id so the attempt closes with it instead of staying open until lease expiry."""
    log = install_fake_executor(monkeypatch, sessionmaker_)
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "running", variables=variables)
    r = await exec_api.claim(eid, "resume")
    assert r.status_code == 422
    assert jbody(r) == {"detail": "decision is required when kind='resume'"}
    assert log.resumes == [] and log.runs == []
    row = await db.must(eid)
    assert row.status == "failed" and row.failure_code == "execution_error"
    assert row.error == "Resume claimed without a decision."
    assert row.lease_owner is None and row.lease_expires_at is None
    assert row.completed_at is not None and row.delivery_attempts == 1
    assert row.variables == variables
    (attempt,) = await db.raw(
        "SELECT result, failure_code, ended_at FROM execution_attempts WHERE execution_id = :e",
        e=eid,
    )
    assert attempt[0] == "failed" and attempt[1] == "execution_error" and attempt[2] is not None


@pytest.mark.parametrize("kind", ["run", "resume"])
async def test_executor_crash_marks_row_failed_with_generic_response(
    exec_api: ExecApi,
    member: Member,
    db: Db,
    sessionmaker_: Any,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)

    async def _boom(*_args: Any) -> None:
        raise RuntimeError("secret-detail kaput")

    log.on_run = _boom
    log.on_resume = _boom
    wid = await db.seed_workflow(member)
    eid = await _seed(
        db,
        wid,
        member,
        "queued" if kind == "run" else "running",
        variables={"_resume_decision": "approved"},
    )
    bystander = await _seed(db, wid, member, "running", lease_owner="other")
    before_bystander = db.snapshot(await db.must(bystander))

    r = await exec_api.claim(eid, kind)
    assert r.status_code == 500
    assert jbody(r) == {
        "detail": f"execution {eid} crashed during {kind}; see application logs for details"
    }
    assert "secret-detail" not in r.text
    row = await db.must(eid)
    assert row.status == "failed" and row.error == "RuntimeError: secret-detail kaput"
    assert row.failure_code == "execution_error"
    assert row.completed_at is not None and row.completed_at > ago(minutes=1)
    assert row.lease_owner is None and row.lease_expires_at is None and row.running_since is None
    assert row.delivery_attempts == 1
    # exactly the crash handler's SET list, measured from the claimed row the fake saw
    (claimed,) = log.seen
    assert claimed["status"] == "running" and claimed["lease_owner"] is not None
    after = db.snapshot(row)
    assert {k for k in claimed if claimed[k] != after[k]} == {
        "status",
        "error",
        "failure_code",
        "completed_at",
        "lease_owner",
        "lease_expires_at",
        "running_since",
    }
    assert db.snapshot(await db.must(bystander)) == before_bystander
    # a redelivery of the same task is a harmless no-op
    again = await exec_api.claim(eid, kind)
    assert again.status_code == 200 and jbody(again) == ALREADY


async def test_crash_after_concurrent_cancel_is_already_terminal(
    exec_api: ExecApi, member: Member, db: Db, sessionmaker_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = install_fake_executor(monkeypatch, sessionmaker_)
    state: dict[str, Any] = {}

    async def _cancel_then_crash(execution_id: str) -> None:
        await db.set_execution(
            execution_id,
            status="canceled",
            error="Execution canceled by user request.",
            completed_at=ago(seconds=2),
            lease_owner=None,
            lease_expires_at=None,
            running_since=None,
        )
        state["canceled"] = db.snapshot(await db.must(execution_id))
        raise RuntimeError("late crash")

    log.on_run = _cancel_then_crash
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "queued")
    r = await exec_api.claim(eid, "run")
    assert r.status_code == 200 and jbody(r) == {"status": "already_terminal"}
    row = await db.must(eid)
    assert row.status == "canceled"
    assert db.snapshot(row) == state["canceled"]


async def test_crash_handler_write_failure_leaves_the_lease_for_the_sweeper(
    exec_api: ExecApi,
    adapter: Phase4aAdapter,
    member: Member,
    db: Db,
    sessionmaker_: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If persisting `failed` itself raises, the original crash still answers 500 (generic
    detail) and the freshly committed lease stays for the lease sweeper."""
    log = install_fake_executor(monkeypatch, sessionmaker_)

    async def _crash(_eid: str) -> None:
        adapter.break_guarded_failure_write()
        raise RuntimeError("original crash")

    log.on_run = _crash
    wid = await db.seed_workflow(member)
    eid = await _seed(db, wid, member, "queued")
    r = await exec_api.claim(eid, "run")
    assert r.status_code == 500
    assert jbody(r) == {
        "detail": f"execution {eid} crashed during run; see application logs for details"
    }
    row = await db.must(eid)
    assert row.status == "running" and row.error is None and row.failure_code is None
    assert row.completed_at is None and row.delivery_attempts == 1
    assert WORKER_ID.match(row.lease_owner or "") and row.lease_expires_at is not None
    assert row.running_since is not None
