"""resume_if_waiting (G3/G4): column-exact transitions and races on real Postgres."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import Approval, ApprovalDecision, Workflow, WorkflowExecution
from src.db.repos import approvals as approvals_repo
from src.db.repos import executions as repo

pytestmark = pytest.mark.integration

REPEATS = 10
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
LEASE = datetime(2030, 1, 1, 11, 0, 0, tzinfo=UTC)
SINCE_TS = datetime(2030, 1, 1, 9, 0, 0, tzinfo=UTC)
PAUSED: dict[str, Any] = {
    "a": 1,
    "_pending_approval_node": "n1",
    "_pending_approval_since": "2030-01-01T09:00:00Z",
}
PENDING = repo.PendingInstance(node_id="n1", since="2030-01-01T09:00:00Z")
STATUSES = ("queued", "running", "waiting_approval", "completed", "failed", "canceled")


@pytest.fixture
async def sm(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def wid(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[str]:
    async with sm() as s:
        w = Workflow(name="resume", nodes=[], edges=[])
        s.add(w)
        await s.commit()
        workflow_id = w.id
    try:
        yield workflow_id
    finally:
        async with sm() as s:
            await s.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await s.commit()


async def _seed(sm: async_sessionmaker[AsyncSession], wid: str, status: str, **kw: Any) -> str:
    values: dict[str, Any] = {
        "workflow_id": wid,
        "user_id": "owner",
        "status": status,
        "node_results": {},
        "variables": dict(PAUSED),
        "thread_id": f"th-{uuid.uuid4()}",
        "lease_owner": "old-worker",
        "lease_expires_at": LEASE,
        "running_since": SINCE_TS,
        "delivery_attempts": 1,
        "current_node_id": "n1",
    }
    values.update(kw)
    async with sm() as s:
        row = WorkflowExecution(**values)
        s.add(row)
        await s.commit()
        return row.id


async def _snap(sm: async_sessionmaker[AsyncSession], eid: str) -> dict[str, Any]:
    async with sm() as s:
        row = (
            await s.execute(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == eid)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return {c.key: getattr(row, c.key) for c in inspect(WorkflowExecution).column_attrs}


def _changed(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {k: after[k] for k in after if after[k] != before[k]}


def _resume_of(
    eid: str, decision: str = "approved", pending: repo.PendingInstance | None = None
) -> Callable[[AsyncSession], Awaitable[bool]]:
    async def op(s: AsyncSession) -> bool:
        return await repo.resume_if_waiting(
            s, eid, decision=decision, now=NOW, expected_pending=pending
        )

    return op


async def _run(
    sm: async_sessionmaker[AsyncSession], fn: Callable[[AsyncSession], Awaitable[Any]]
) -> Any:
    async with sm() as s:
        result = await fn(s)
        await s.commit()
        return result


async def test_resume_wins_only_from_waiting_approval(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for status in STATUSES:
        eid = await _seed(sm, wid, status)
        before = await _snap(sm, eid)
        assert await _run(sm, _resume_of(eid)) is (status == "waiting_approval")
        after = await _snap(sm, eid)
        if status != "waiting_approval":
            assert after == before, status
        else:
            assert _changed(before, after) == {
                "status": "running",
                "running_since": NOW,
                "lease_owner": None,
                "lease_expires_at": None,
                "variables": {**PAUSED, "_resume_decision": "approved"},
            }


async def test_resume_with_expected_pending_also_wins_only_from_waiting_approval(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for status in STATUSES:
        eid = await _seed(sm, wid, status)
        before = await _snap(sm, eid)
        assert await _run(sm, _resume_of(eid, pending=PENDING)) is (status == "waiting_approval")
        if status != "waiting_approval":
            assert await _snap(sm, eid) == before, status


async def test_resume_overwrites_an_existing_decision_key(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    eid = await _seed(
        sm, wid, "waiting_approval", variables={**PAUSED, "_resume_decision": "rejected"}
    )
    assert await _run(sm, _resume_of(eid, "approved"))
    assert (await _snap(sm, eid))["variables"] == {**PAUSED, "_resume_decision": "approved"}


async def test_resume_with_expected_pending(sm: async_sessionmaker[AsyncSession], wid: str) -> None:
    cases = [
        (PENDING, True),
        (repo.PendingInstance("other", PENDING.since), False),
        (repo.PendingInstance(PENDING.node_id, "2031-01-01T00:00:00Z"), False),
    ]
    for pending, expect in cases:
        eid = await _seed(sm, wid, "waiting_approval")
        before = await _snap(sm, eid)
        assert await _run(sm, _resume_of(eid, pending=pending)) is expect
        if not expect:
            assert await _snap(sm, eid) == before
    eid = await _seed(sm, wid, "waiting_approval", variables={"a": 1})
    before = await _snap(sm, eid)
    assert await _run(sm, _resume_of(eid, pending=PENDING)) is False
    assert await _snap(sm, eid) == before


async def test_resume_on_missing_row(sm: async_sessionmaker[AsyncSession]) -> None:
    assert await _run(sm, _resume_of("nope")) is False


async def test_stamp_merges_into_current_variables(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    """A change to another key made after seeding survives the stamp."""
    eid = await _seed(sm, wid, "waiting_approval")
    async with sm() as s:
        await s.execute(
            sa_update(WorkflowExecution)
            .where(WorkflowExecution.id == eid)
            .values(variables={**PAUSED, "added_later": True})
        )
        await s.commit()
    assert await _run(sm, _resume_of(eid))
    assert (await _snap(sm, eid))["variables"] == {
        **PAUSED,
        "added_later": True,
        "_resume_decision": "approved",
    }


async def test_bystander_row_untouched(sm: async_sessionmaker[AsyncSession], wid: str) -> None:
    eid = await _seed(sm, wid, "waiting_approval")
    other = await _seed(sm, wid, "waiting_approval")
    before = await _snap(sm, other)
    assert await _run(sm, _resume_of(eid))
    assert await _snap(sm, other) == before


async def test_five_concurrent_resumes_have_exactly_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "waiting_approval")
        results = await asyncio.gather(*[_run(sm, _resume_of(eid)) for _ in range(5)])
        assert results.count(True) == 1
        assert (await _snap(sm, eid))["status"] == "running"


async def test_opposite_decisions_with_the_same_token_have_one_winner(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "waiting_approval")
        ops = [_resume_of(eid, "approved", PENDING) for _ in range(3)] + [
            _resume_of(eid, "rejected", PENDING) for _ in range(3)
        ]
        results = await asyncio.gather(*[_run(sm, op) for op in ops])
        assert results.count(True) == 1
        winner = ("approved", "rejected")[results.index(True) // 3]
        assert (await _snap(sm, eid))["variables"]["_resume_decision"] == winner


async def test_expected_pending_mismatch_never_wins_a_race(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    stale = repo.PendingInstance(PENDING.node_id, "2029-01-01T00:00:00Z")
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "waiting_approval")
        ops = [_resume_of(eid, pending=stale) for _ in range(3)] + [_resume_of(eid)]
        results = await asyncio.gather(*[_run(sm, op) for op in ops])
        assert results == [False, False, False, True]


async def test_resume_vs_expiry_sweep_exactly_one_wins(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "waiting_approval")

        async def sweep(s: AsyncSession, eid: str = eid) -> bool:
            return await repo.fail_expired_approval_if_unchanged(
                s, eid, variables_snapshot=PAUSED, error="expired", now=NOW
            )

        resumed, expired = await asyncio.gather(_run(sm, _resume_of(eid)), _run(sm, sweep))
        assert resumed != expired
        assert (await _snap(sm, eid))["status"] == ("running" if resumed else "failed")


async def test_resume_vs_cancel(sm: async_sessionmaker[AsyncSession], wid: str) -> None:
    for _ in range(REPEATS):
        eid = await _seed(sm, wid, "waiting_approval")

        async def cancel(s: AsyncSession, eid: str = eid) -> bool:
            return await repo.cancel_if_active(s, eid, error="canceled", now=NOW)

        _resumed, canceled = await asyncio.gather(_run(sm, _resume_of(eid)), _run(sm, cancel))
        assert canceled is True
        assert (await _snap(sm, eid))["status"] == "canceled"


async def test_claim_sees_the_decision_the_instant_the_transition_commits(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    eid = await _seed(sm, wid, "waiting_approval")
    assert await _run(sm, _resume_of(eid, "rejected"))

    async def claim(s: AsyncSession) -> repo.ClaimedExecution | None:
        return await repo.claim(
            s,
            eid,
            kind="resume",
            worker_id="w1",
            now=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
        )

    claimed = await _run(sm, claim)
    assert claimed is not None and claimed.variables["_resume_decision"] == "rejected"


async def test_rollback_undoes_both_the_transition_and_the_approval_row(
    sm: async_sessionmaker[AsyncSession], wid: str
) -> None:
    eid = await _seed(sm, wid, "waiting_approval")
    before = await _snap(sm, eid)
    async with sm() as s:
        assert await repo.resume_if_waiting(s, eid, decision="approved", now=NOW)
        await approvals_repo.create(
            s,
            execution_id=eid,
            node_id="n1",
            decision=ApprovalDecision.APPROVED,
            approver_user_id=None,
            approver_email=None,
            via_email_link=False,
            note=None,
        )
        await s.rollback()
    assert await _snap(sm, eid) == before
    async with sm() as s:
        rows = (await s.execute(select(Approval).where(Approval.execution_id == eid))).all()
    assert rows == []
