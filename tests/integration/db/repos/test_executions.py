import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists
from src.db.models import (
    Approval,
    ApprovalDecision,
    ExecutionEvent,
    LangGraphCheckpoint,
    LangGraphCheckpointWrite,
    Workflow,
    WorkflowExecution,
)
from src.db.repos import executions as repo

pytestmark = pytest.mark.integration

ALL_STATUSES = ("queued", "running", "waiting_approval", "completed", "failed", "canceled")
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
LEASE = datetime(2030, 1, 1, 11, 0, 0, tzinfo=UTC)
RUNNING_SINCE = datetime(2030, 1, 1, 9, 0, 0, tzinfo=UTC)
OLD = datetime(2029, 6, 1, 0, 0, 0, tzinfo=UTC)


def _id(prefix: str = "x") -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


async def _workflow(session: AsyncSession) -> str:
    w = Workflow(name="W", nodes=[], edges=[])
    session.add(w)
    await session.flush()
    return w.id


async def _exec(session: AsyncSession, wid: str, status: str = "queued", **kw: Any) -> str:
    values: dict[str, Any] = {
        "workflow_id": wid,
        "user_id": "owner",
        "status": status,
        "node_results": {"n": 1},
        "variables": {"a": 1},
        "input": {"i": 1},
        "thread_id": _id("th"),
        "current_node_id": "n1",
        "lease_owner": "w",
        "lease_expires_at": LEASE,
        "running_since": RUNNING_SINCE,
        "delivery_attempts": 2,
    }
    values.update(kw)
    row = WorkflowExecution(**values)
    session.add(row)
    await session.flush()
    return row.id


async def _snap(session: AsyncSession, execution_id: str) -> dict[str, Any]:
    cols = list(WorkflowExecution.__table__.c)
    row = (await session.execute(select(*cols).where(WorkflowExecution.id == execution_id))).one()
    return dict(row._mapping)  # pyright: ignore[reportPrivateUsage]


async def _jtype(session: AsyncSession, execution_id: str, column: str) -> tuple[bool, str | None]:
    r = (
        await session.execute(
            text(
                f"SELECT {column} IS NULL, jsonb_typeof({column}) FROM workflow_executions "
                "WHERE id = :i"
            ),
            {"i": execution_id},
        )
    ).one()
    return r[0], r[1]


# --------------------------------------------------------------------------- create / read


async def test_create_returns_a_full_record(session: AsyncSession) -> None:
    wid = await _workflow(session)
    rec = await repo.create(
        session, workflow_id=wid, user_id="u1", input={"x": 1}, thread_id=_id("th")
    )
    assert rec.status == "queued" and rec.workflow_id == wid and rec.user_id == "u1"
    assert rec.id and len(rec.id) == 36
    assert rec.started_at.tzinfo is not None
    assert rec.node_results == {} and rec.variables == {} and rec.input == {"x": 1}
    assert rec.output is None and rec.error is None and rec.failure_code is None
    assert rec.completed_at is None and rec.delivery_attempts == 0
    assert rec.lease_owner is None and rec.lease_expires_at is None and rec.running_since is None
    assert rec.idempotency_key is None and rec.original_owner_email is None
    assert await repo.get(session, rec.id) == rec


async def test_create_stores_an_absent_input_as_json_null(session: AsyncSession) -> None:
    wid = await _workflow(session)
    rec = await repo.create(session, workflow_id=wid, user_id="u1", input=None, thread_id=_id("th"))
    assert await _jtype(session, rec.id, "input") == (False, "null")


async def test_create_duplicate_idempotency_key_raises_and_session_stays_usable(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    await repo.create(
        session, workflow_id=wid, user_id="u1", input=1, thread_id=_id("th"), idempotency_key="k"
    )
    with pytest.raises(AlreadyExists) as exc:
        await repo.create(
            session,
            workflow_id=wid,
            user_id="u1",
            input=2,
            thread_id=_id("th"),
            idempotency_key="k",
        )
    assert exc.value.constraint == "workflow_executions_workflow_id_user_id_idempotency_key_key"
    other = await repo.create(
        session, workflow_id=wid, user_id="u1", input=3, thread_id=_id("th"), idempotency_key="k2"
    )
    assert await repo.get_status(session, other.id) == "queued"


async def test_idempotency_keys_are_per_user_and_null_users_never_collide(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    a = await repo.create(
        session, workflow_id=wid, user_id="u1", input=1, thread_id=_id("th"), idempotency_key="k"
    )
    b = await repo.create(
        session, workflow_id=wid, user_id="u2", input=1, thread_id=_id("th"), idempotency_key="k"
    )
    n1 = await repo.create(
        session, workflow_id=wid, user_id=None, input=1, thread_id=_id("th"), idempotency_key="k"
    )
    n2 = await repo.create(
        session, workflow_id=wid, user_id=None, input=1, thread_id=_id("th"), idempotency_key="k"
    )
    assert len({a.id, b.id, n1.id, n2.id}) == 4


async def test_find_by_idempotency_key_is_scoped_to_workflow_user_and_key(
    session: AsyncSession,
) -> None:
    wid, other_wid = await _workflow(session), await _workflow(session)
    a = await repo.create(
        session, workflow_id=wid, user_id="u1", input=1, thread_id=_id("th"), idempotency_key="k"
    )
    await repo.create(
        session, workflow_id=wid, user_id="u2", input=1, thread_id=_id("th"), idempotency_key="k"
    )
    found = await repo.find_by_idempotency_key(session, workflow_id=wid, user_id="u1", key="k")
    assert found is not None and found.id == a.id
    assert (
        await repo.find_by_idempotency_key(session, workflow_id=wid, user_id="u1", key="z") is None
    )
    assert (
        await repo.find_by_idempotency_key(session, workflow_id=other_wid, user_id="u1", key="k")
        is None
    )
    assert (
        await repo.find_by_idempotency_key(session, workflow_id=wid, user_id="u3", key="k") is None
    )


async def test_get_and_get_status_for_missing_row(session: AsyncSession) -> None:
    assert await repo.get(session, "missing") is None
    assert await repo.get_status(session, "missing") is None


async def test_get_reads_the_database_not_the_identity_map(session: AsyncSession) -> None:
    wid = await _workflow(session)
    eid = await _exec(session, wid, "running")
    first = await repo.get(session, eid)
    assert first is not None and first.status == "running"
    await repo.cancel_if_active(session, eid, error="x", now=NOW)
    again = await repo.get(session, eid)
    assert again is not None and again.status == "canceled"


async def test_list_page_filters_count_order_and_offset(session: AsyncSession) -> None:
    wid, other = await _workflow(session), await _workflow(session)
    owner = _id("own")
    base = datetime(2031, 1, 1, tzinfo=UTC)
    ids: list[str] = []
    for i in (2, 0, 1):  # inserted out of order
        ids.append(
            await _exec(
                session,
                wid,
                "completed" if i else "failed",
                user_id=owner,
                started_at=base + timedelta(minutes=i),
            )
        )
    await _exec(session, wid, "completed", user_id="someone-else", started_at=base)
    await _exec(session, other, "completed", user_id=owner, started_at=base)

    rows, total = await repo.list_page(
        session, owner_id=owner, workflow_id=wid, status=None, limit=10, offset=0
    )
    assert total == 3
    assert [r.id for r in rows] == [ids[0], ids[2], ids[1]]  # minutes 2, 1, 0
    rows, total = await repo.list_page(
        session, owner_id=owner, workflow_id=wid, status=None, limit=1, offset=1
    )
    assert total == 3 and [r.id for r in rows] == [ids[2]]
    rows, total = await repo.list_page(
        session, owner_id=owner, workflow_id=wid, status="failed", limit=10, offset=0
    )
    assert total == 1 and [r.id for r in rows] == [ids[1]]
    _, all_owner = await repo.list_page(
        session, owner_id=owner, workflow_id=None, status=None, limit=10, offset=0
    )
    assert all_owner == 4
    _, everyone = await repo.list_page(
        session, owner_id=None, workflow_id=wid, status=None, limit=10, offset=0
    )
    assert everyone == 4


async def test_list_refs_by_ids_and_owner(session: AsyncSession) -> None:
    wid = await _workflow(session)
    owner = _id("own")
    a = await _exec(session, wid, "running", user_id=owner)
    b = await _exec(session, wid, "completed", user_id="other")
    refs = await repo.list_refs(session, ids=[a, b, "missing"], owner_id=None)
    assert {(r.id, r.status) for r in refs} == {(a, "running"), (b, "completed")}
    assert all(r.thread_id for r in refs)
    mine = await repo.list_refs(session, ids=[a, b], owner_id=owner)
    assert [r.id for r in mine] == [a]
    everything = await repo.list_refs(session, ids=None, owner_id=owner)
    assert a in {r.id for r in everything} and b not in {r.id for r in everything}


# --------------------------------------------------------------------------- delete


async def _artifacts(session: AsyncSession, execution_id: str, thread_id: str) -> None:
    session.add(ExecutionEvent(execution_id=execution_id, seq=1, type="t", payload={}))
    session.add(
        LangGraphCheckpoint(
            thread_id=thread_id,
            checkpoint_ns="",
            checkpoint_id=_id("c"),
            checkpoint=b"c",
            metadata_=b"m",
        )
    )
    session.add(
        LangGraphCheckpointWrite(
            thread_id=thread_id,
            checkpoint_ns="",
            checkpoint_id=_id("c"),
            task_id="t",
            idx=0,
            channel="ch",
            value=b"v",
        )
    )
    session.add(
        Approval(execution_id=execution_id, node_id="n", decision=ApprovalDecision.APPROVED)
    )
    await session.flush()


async def _artifact_counts(session: AsyncSession, execution_id: str, thread_id: str) -> list[int]:
    out: list[int] = []
    for model, col, val in (
        (ExecutionEvent, ExecutionEvent.execution_id, execution_id),
        (LangGraphCheckpoint, LangGraphCheckpoint.thread_id, thread_id),
        (LangGraphCheckpointWrite, LangGraphCheckpointWrite.thread_id, thread_id),
        (Approval, Approval.execution_id, execution_id),
    ):
        out.append(
            await session.scalar(select(func.count()).select_from(model).where(col == val)) or 0
        )
    return out


async def test_delete_terminal_deletes_only_non_active_rows_and_their_artifacts(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    rows: dict[str, tuple[str, str]] = {}
    for status in ALL_STATUSES:
        eid = await _exec(session, wid, status)
        tid = (await _snap(session, eid))["thread_id"]
        await _artifacts(session, eid, tid)
        rows[status] = (eid, tid)
    bystander = await _exec(session, wid, "completed")
    b_tid = (await _snap(session, bystander))["thread_id"]
    await _artifacts(session, bystander, b_tid)
    before = await _snap(session, bystander)

    ids = [e for e, _ in rows.values()]
    deleted = await repo.delete_terminal(session, ids)

    assert {d.id for d in deleted} == {rows[s][0] for s in ("completed", "failed", "canceled")}
    assert {d.status for d in deleted} == {"completed", "failed", "canceled"}
    assert all(d.thread_id == dict(rows.values())[d.id] for d in deleted)
    for status in ("completed", "failed", "canceled"):
        eid, tid = rows[status]
        assert await repo.get(session, eid) is None
        assert await _artifact_counts(session, eid, tid) == [0, 0, 0, 0]
    for status in ("queued", "running", "waiting_approval"):
        eid, tid = rows[status]
        assert await repo.get_status(session, eid) == status
        assert await _artifact_counts(session, eid, tid) == [1, 1, 1, 1]
    assert await _snap(session, bystander) == before
    assert await _artifact_counts(session, bystander, b_tid) == [1, 1, 1, 1]


async def test_delete_terminal_with_no_ids_is_a_noop(session: AsyncSession) -> None:
    assert await repo.delete_terminal(session, []) == []


# --------------------------------------------------------------------------- guarded updates


async def _g1(s: AsyncSession, eid: str) -> bool:
    return await repo.mark_enqueue_failed_if_queued(s, eid, error="boom", now=NOW)


async def _g2(s: AsyncSession, eid: str) -> bool:
    return await repo.cancel_if_active(s, eid, error="canceled", now=NOW)


async def _g6(s: AsyncSession, eid: str) -> bool:
    return await repo.mark_failed_if_running(
        s, eid, error="E: x", failure_code="execution_timeout", now=NOW
    )


async def _g7(s: AsyncSession, eid: str) -> bool:
    return await repo.mark_completed_if_running(
        s, eid, output={"r": 2}, variables={"v": 2}, node_results={"n": 2}, now=NOW
    )


async def _g8(s: AsyncSession, eid: str) -> bool:
    return await repo.mark_waiting_approval_if_running(s, eid, variables={"a": 1, "p": "n"})


async def _g10(s: AsyncSession, eid: str) -> bool:
    return await repo.fail_orphaned_if_unchanged(
        s, eid, cutoff=RUNNING_SINCE + timedelta(hours=1), error="orphan", now=NOW
    )


async def _g11(s: AsyncSession, eid: str) -> bool:
    return await repo.fail_expired_approval_if_unchanged(
        s, eid, variables_snapshot={"a": 1}, error="expired", now=NOW
    )


async def _g12(s: AsyncSession, eid: str) -> bool:
    return await repo.fail_expired_lease_if_unchanged(
        s, eid, lease_expires_at=LEASE, error="lease", now=NOW
    )


_NO_LEASE = {"lease_owner": None, "lease_expires_at": None, "running_since": None}

# name, function, winning statuses, seed overrides, exact expected changes
_GUARDED: list[
    tuple[
        str,
        Callable[[AsyncSession, str], Awaitable[bool]],
        set[str],
        dict[str, Any],
        dict[str, Any],
    ]
] = [
    (
        "G1 mark_enqueue_failed_if_queued",
        _g1,
        {"queued"},
        {},
        {"status": "failed", "error": "boom", "completed_at": NOW},
    ),
    (
        "G2 cancel_if_active",
        _g2,
        {"queued", "running", "waiting_approval"},
        {},
        {"status": "canceled", "error": "canceled", "completed_at": NOW, **_NO_LEASE},
    ),
    (
        "G6 mark_failed_if_running",
        _g6,
        {"running"},
        {},
        {
            "status": "failed",
            "error": "E: x",
            "failure_code": "execution_timeout",
            "completed_at": NOW,
            **_NO_LEASE,
        },
    ),
    (
        "G7 mark_completed_if_running",
        _g7,
        {"running"},
        {},
        {
            "status": "completed",
            "output": {"r": 2},
            "variables": {"v": 2},
            "node_results": {"n": 2},
            "completed_at": NOW,
            **_NO_LEASE,
        },
    ),
    (
        "G8 mark_waiting_approval_if_running",
        _g8,
        {"running"},
        {"completed_at": OLD},  # untouched by G8
        {"status": "waiting_approval", "variables": {"a": 1, "p": "n"}, **_NO_LEASE},
    ),
    (
        "G10 fail_orphaned_if_unchanged",
        _g10,
        {"running"},
        {"lease_expires_at": None},
        {
            "status": "failed",
            "failure_code": "orphaned_running",
            "error": "orphan",
            "completed_at": NOW,
            "lease_owner": None,
            "running_since": None,
        },
    ),
    (
        "G11 fail_expired_approval_if_unchanged",
        _g11,
        {"waiting_approval"},
        {},  # lease columns stay set: G11 leaves them alone
        {"status": "failed", "error": "expired", "completed_at": NOW},
    ),
    (
        "G12 fail_expired_lease_if_unchanged",
        _g12,
        {"running"},
        {},
        {
            "status": "failed",
            "failure_code": "lease_expired",
            "error": "lease",
            "completed_at": NOW,
            **_NO_LEASE,
        },
    ),
]


@pytest.mark.parametrize("case", _GUARDED, ids=[c[0].split()[0] for c in _GUARDED])
@pytest.mark.parametrize("status", ALL_STATUSES)
async def test_guarded_update_sets_exactly_its_columns_or_leaves_the_row_untouched(
    session: AsyncSession,
    case: tuple[
        str,
        Callable[[AsyncSession, str], Awaitable[bool]],
        set[str],
        dict[str, Any],
        dict[str, Any],
    ],
    status: str,
) -> None:
    name, fn, winners, seed, expected = case
    wid = await _workflow(session)
    eid = await _exec(session, wid, status, **seed)
    bystander = await _exec(session, wid, status, **seed)
    before, bystander_before = await _snap(session, eid), await _snap(session, bystander)

    won = await fn(session, eid)

    after = await _snap(session, eid)
    assert won is (status in winners), name
    if status in winners:
        changed = {k for k in before if before[k] != after[k]}
        assert changed == set(expected), (name, changed ^ set(expected))
        for k, v in expected.items():
            assert after[k] == v, (name, k)
    else:
        assert after == before, name
    assert await _snap(session, bystander) == bystander_before


async def test_mark_completed_with_no_output_stores_json_null(session: AsyncSession) -> None:
    wid = await _workflow(session)
    eid = await _exec(session, wid, "running")
    assert await repo.mark_completed_if_running(
        session, eid, output=None, variables={}, node_results={}, now=NOW
    )
    assert await _jtype(session, eid, "output") == (False, "null")


async def test_fail_expired_lease_with_a_different_scanned_expiry_loses(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    eid = await _exec(session, wid, "running")
    before = await _snap(session, eid)
    assert not await repo.fail_expired_lease_if_unchanged(
        session, eid, lease_expires_at=LEASE - timedelta(seconds=1), error="x", now=NOW
    )
    assert await _snap(session, eid) == before


async def test_fail_orphaned_ignores_a_row_younger_than_the_cutoff_or_leased(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    young = await _exec(session, wid, "running", lease_expires_at=None)
    leased = await _exec(session, wid, "running", running_since=OLD)
    cutoff = RUNNING_SINCE - timedelta(hours=1)
    assert not await repo.fail_orphaned_if_unchanged(
        session, young, cutoff=cutoff, error="x", now=NOW
    )
    assert not await repo.fail_orphaned_if_unchanged(
        session, leased, cutoff=cutoff + timedelta(days=400), error="x", now=NOW
    )


async def test_fail_expired_approval_compares_the_whole_variables_document(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    eid = await _exec(session, wid, "waiting_approval", variables={"b": [1, 2], "a": {"z": 1}})
    stale = {"a": {"z": 1}, "b": [1, 2, 3]}
    assert not await repo.fail_expired_approval_if_unchanged(
        session, eid, variables_snapshot=stale, error="x", now=NOW
    )
    # JSONB equality ignores key order
    assert await repo.fail_expired_approval_if_unchanged(
        session, eid, variables_snapshot={"a": {"z": 1}, "b": [1, 2]}, error="x", now=NOW
    )


# --------------------------------------------------------------------------- scans


async def test_list_scans_return_exactly_the_candidates(session: AsyncSession) -> None:
    wid = await _workflow(session)
    cutoff = RUNNING_SINCE + timedelta(hours=1)
    orphan = await _exec(session, wid, "running", lease_expires_at=None, lease_owner=None)
    await _exec(session, wid, "running")  # leased
    await _exec(session, wid, "running", lease_expires_at=None, running_since=cutoff)  # not older
    await _exec(session, wid, "queued", lease_expires_at=None)
    assert orphan in {r.id for r in await repo.list_orphaned_running(session, cutoff=cutoff)}
    assert (
        len(
            [
                r
                for r in await repo.list_orphaned_running(session, cutoff=cutoff)
                if r.thread_id and r.workflow_id == wid
            ]
        )
        == 1
    )

    waiting = await _exec(session, wid, "waiting_approval")
    assert waiting in {r.id for r in await repo.list_waiting_approval(session)}
    assert not {r.id for r in await repo.list_waiting_approval(session)} & {orphan}

    expired = await _exec(session, wid, "running", lease_expires_at=LEASE)
    live = await _exec(session, wid, "running", lease_expires_at=LEASE + timedelta(days=3650))
    ids = {r.id for r in await repo.list_expired_leases(session, now=NOW)}
    assert expired in ids and live not in ids and orphan not in ids
    rec = next(r for r in await repo.list_expired_leases(session, now=NOW) if r.id == expired)
    assert rec.lease_expires_at == LEASE  # round-trips exactly, aware UTC


# --------------------------------------------------------------------------- claim


@pytest.mark.parametrize("kind", ["run", "resume"])
@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("leased", [False, True])
async def test_claim_wins_only_for_the_kinds_status_and_an_unleased_row(
    session: AsyncSession, kind: str, status: str, leased: bool
) -> None:
    wid = await _workflow(session)
    lease: dict[str, Any] = (
        {} if leased else {"lease_owner": None, "lease_expires_at": None, "running_since": None}
    )
    eid = await _exec(session, wid, status, **lease)
    bystander = await _exec(session, wid, status, **lease)
    before, b_before = await _snap(session, eid), await _snap(session, bystander)
    new_lease = NOW + timedelta(minutes=5)

    got = await repo.claim(
        session,
        eid,
        kind=kind,  # pyright: ignore[reportArgumentType]
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=new_lease,
    )

    should_win = (not leased) and status == ("queued" if kind == "run" else "running")
    after = await _snap(session, eid)
    if should_win:
        assert got is not None and got.id == eid and got.variables == {"a": 1}
        expected: dict[str, Any] = {
            "status": "running",
            "lease_owner": "worker-1",
            "lease_expires_at": new_lease,
            "running_since": NOW,
            "delivery_attempts": 3,
        }
        changed = {k for k in before if before[k] != after[k]}
        assert changed == {k for k in expected if before[k] != expected[k]}
        for k, v in expected.items():
            assert after[k] == v
    else:
        assert got is None
        assert after == before
    assert await _snap(session, bystander) == b_before


async def test_claim_of_a_missing_row_is_none(session: AsyncSession) -> None:
    assert (
        await repo.claim(
            session, "missing", kind="run", worker_id="w", now=NOW, lease_expires_at=NOW
        )
        is None
    )


# --------------------------------------------------------------------------- migration helpers


async def test_insert_if_absent_inserts_once_and_leaves_input_output_sql_null(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    eid = _id("mig")
    values: repo.ExecutionInsert = {
        "id": eid,
        "workflow_id": wid,
        "status": "completed",
        "thread_id": _id("th"),
        "node_results": {},
        "variables": {},
        "original_owner_email": "a@example.com",
    }
    assert await repo.insert_if_absent(session, values) is True
    first = await _snap(session, eid)
    assert await _jtype(session, eid, "input") == (True, None)
    assert await _jtype(session, eid, "output") == (True, None)
    assert first["delivery_attempts"] == 0 and first["started_at"] is not None
    changed: repo.ExecutionInsert = {**values, "status": "failed", "thread_id": _id("th")}
    assert await repo.insert_if_absent(session, changed) is False
    assert await _snap(session, eid) == first


async def test_claim_orphans_by_email_is_idempotent_and_skips_owned_rows(
    session: AsyncSession,
) -> None:
    wid = await _workflow(session)
    email = f"{_id('o')}@example.com"
    orphan = await _exec(session, wid, "completed", user_id=None, original_owner_email=email)
    owned = await _exec(session, wid, "completed", user_id="someone", original_owner_email=email)
    other = await _exec(
        session, wid, "completed", user_id=None, original_owner_email="x@example.com"
    )
    other_before = await _snap(session, other)
    owned_before = await _snap(session, owned)

    assert await repo.claim_orphans_by_email(session, email, "new-user") == 1
    assert (await _snap(session, orphan))["user_id"] == "new-user"
    assert await repo.claim_orphans_by_email(session, email, "new-user") == 0
    assert await _snap(session, owned) == owned_before
    assert await _snap(session, other) == other_before
