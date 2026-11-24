"""Group 4: DELETE /executions/{id} and POST /executions/delete-bulk."""

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    ExecApi,
    Member,
    jbody,
    not_found,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

ACTIVE = ["queued", "running", "waiting_approval"]
TERMINAL = ["completed", "failed", "canceled"]


async def _with_artifacts(db: Db, wid: str, owner: Member | None, status: str) -> tuple[str, str]:
    """An execution plus events, checkpoint rows in both tables and an approval. Returns
    (execution_id, thread_id)."""
    eid = await db.seed_execution(wid, owner, status=status)
    row = await db.must(eid)
    await db.add_events(
        eid, [(1, "workflow_started", {"status": "running"}), (2, "node_started", {})]
    )
    await db.add_checkpoint(row.thread_id)
    await db.add_approval(eid)
    return eid, row.thread_id


async def _artifacts(db: Db, eid: str, thread_id: str) -> tuple[int, int, int, int, int]:
    ckpt, writes = await db.checkpoint_counts(thread_id)
    return (
        await db.count_executions([eid]),
        await db.event_count([eid]),
        ckpt,
        writes,
        await db.approval_count(eid),
    )


FULL = (1, 2, 1, 1, 1)
GONE = (0, 0, 0, 0, 0)


@pytest.mark.parametrize("status", TERMINAL)
async def test_single_delete_removes_row_and_artifacts_only_for_that_row(
    exec_api: ExecApi, member: Member, db: Db, status: str
) -> None:
    wid = await db.seed_workflow(member)
    target, target_thread = await _with_artifacts(db, wid, member, status)
    other, other_thread = await _with_artifacts(db, wid, member, "completed")
    before_other = db.snapshot(await db.must(other))
    assert await _artifacts(db, target, target_thread) == FULL

    r = await exec_api.delete(member, target)
    assert r.status_code == 204 and r.content == b""
    assert await _artifacts(db, target, target_thread) == GONE
    assert await _artifacts(db, other, other_thread) == FULL
    assert db.snapshot(await db.must(other)) == before_other
    # the second delete is a 404
    again = await exec_api.delete(member, target)
    assert again.status_code == 404 and jbody(again) == {"detail": not_found(target)}


@pytest.mark.parametrize("status", ACTIVE)
async def test_single_delete_refuses_active_rows_with_exact_detail(
    exec_api: ExecApi, member: Member, db: Db, status: str
) -> None:
    wid = await db.seed_workflow(member)
    eid, thread = await _with_artifacts(db, wid, member, status)
    before = db.snapshot(await db.must(eid))
    r = await exec_api.delete(member, eid)
    assert r.status_code == 409
    assert jbody(r) == {
        "detail": f"Execution {eid!r} is {status!r} — cancel it first, or wait "
        "for it to reach a terminal state, before deleting."
    }
    assert await _artifacts(db, eid, thread) == FULL
    assert db.snapshot(await db.must(eid)) == before


async def test_single_delete_authz(
    exec_api: ExecApi, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid, thread = await _with_artifacts(db, wid, member, "completed")
    r = await exec_api.delete(second_member, eid)
    assert r.status_code == 404 and jbody(r) == {"detail": not_found(eid)}
    assert await _artifacts(db, eid, thread) == FULL
    r = await exec_api.delete(member, "no-such-execution")
    assert r.status_code == 404 and jbody(r) == {"detail": not_found("no-such-execution")}
    # admin may delete another user's row, and an anonymous (user_id NULL) row
    r = await exec_api.delete(admin, eid)
    assert r.status_code == 204
    assert await _artifacts(db, eid, thread) == GONE
    anon, anon_thread = await _with_artifacts(db, wid, None, "failed")
    assert (await exec_api.delete(member, anon)).status_code == 404
    assert (await exec_api.delete(admin, anon)).status_code == 204
    assert await _artifacts(db, anon, anon_thread) == GONE


# --------------------------------------------------------------------------- bulk


async def test_bulk_member_all_in_scope_forbidden(
    client: AsyncClient, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid, thread = await _with_artifacts(db, wid, member, "completed")
    r = await client.post(
        "/executions/delete-bulk", json={"allInScope": True}, headers=member.headers
    )
    assert r.status_code == 403 and jbody(r) == {"detail": "allInScope=true is admin-only."}
    assert await _artifacts(db, eid, thread) == FULL


@pytest.mark.parametrize("body", [{}, {"executionIds": []}, {"executionIds": None}])
async def test_bulk_empty_request_is_422_exact(
    client: AsyncClient, member: Member, body: dict[str, object]
) -> None:
    r = await client.post("/executions/delete-bulk", json=body, headers=member.headers)
    assert r.status_code == 422
    assert jbody(r) == {"detail": "Provide executionIds or set allInScope=true."}


async def test_bulk_mixed_request_exact_counts_and_scoping(
    client: AsyncClient, member: Member, second_member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member, is_public=True)
    own_done, own_done_t = await _with_artifacts(db, wid, member, "completed")
    own_failed, own_failed_t = await _with_artifacts(db, wid, member, "failed")
    own_running, own_running_t = await _with_artifacts(db, wid, member, "running")
    their_a, their_a_t = await _with_artifacts(db, wid, second_member, "completed")
    their_b, their_b_t = await _with_artifacts(db, wid, second_member, "canceled")
    untouched, untouched_t = await _with_artifacts(db, wid, member, "completed")
    before = {i: db.snapshot(await db.must(i)) for i in (own_running, their_a, their_b, untouched)}

    r = await client.post(
        "/executions/delete-bulk",
        json={"executionIds": [their_b, own_done, own_running, their_a, own_failed]},
        headers=member.headers,
    )
    assert r.status_code == 200, r.text
    assert jbody(r) == {"deletedCount": 2, "skippedCount": 3}
    assert await _artifacts(db, own_done, own_done_t) == GONE
    assert await _artifacts(db, own_failed, own_failed_t) == GONE
    for eid, thread in (
        (own_running, own_running_t),
        (their_a, their_a_t),
        (their_b, their_b_t),
        (untouched, untouched_t),
    ):
        assert await _artifacts(db, eid, thread) == FULL
    for i, snap in before.items():
        assert db.snapshot(await db.must(i)) == snap


async def test_bulk_only_active_or_unknown_ids_delete_nothing(
    client: AsyncClient, member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    ids: list[str] = []
    for status in ACTIVE:
        ids.append((await _with_artifacts(db, wid, member, status))[0])
    r = await client.post(
        "/executions/delete-bulk",
        json={"executionIds": [*ids, "ghost-1"]},
        headers=member.headers,
    )
    assert r.status_code == 200 and jbody(r) == {"deletedCount": 0, "skippedCount": 4}
    assert await db.count_executions(ids) == 3
    r = await client.post(
        "/executions/delete-bulk",
        json={"executionIds": ["ghost-1", "ghost-2"]},
        headers=member.headers,
    )
    assert jbody(r) == {"deletedCount": 0, "skippedCount": 2}


async def test_bulk_admin_explicit_ids_cross_user(
    client: AsyncClient, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member, is_public=True)
    a, a_t = await _with_artifacts(db, wid, member, "completed")
    b, b_t = await _with_artifacts(db, wid, second_member, "failed")
    anon, anon_t = await _with_artifacts(db, wid, None, "canceled")
    keep, keep_t = await _with_artifacts(db, wid, second_member, "completed")
    r = await client.post(
        "/executions/delete-bulk", json={"executionIds": [anon, b, a]}, headers=admin.headers
    )
    assert jbody(r) == {"deletedCount": 3, "skippedCount": 0}
    for eid, thread in ((a, a_t), (b, b_t), (anon, anon_t)):
        assert await _artifacts(db, eid, thread) == GONE
    assert await _artifacts(db, keep, keep_t) == FULL


async def test_bulk_admin_all_in_scope_deletes_terminal_rows_only(
    client: AsyncClient, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    """Deletes every terminal execution in the (throwaway) test database."""
    wid = await db.seed_workflow(member, is_public=True)
    gone = [
        await _with_artifacts(db, wid, member, "completed"),
        await _with_artifacts(db, wid, second_member, "failed"),
        await _with_artifacts(db, wid, None, "canceled"),
    ]
    kept = [await _with_artifacts(db, wid, member, s) for s in ACTIVE]
    before = {eid: db.snapshot(await db.must(eid)) for eid, _ in kept}
    r = await client.post(
        "/executions/delete-bulk", json={"allInScope": True}, headers=admin.headers
    )
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == {"deletedCount", "skippedCount"}
    assert body["deletedCount"] >= 3 and body["skippedCount"] == 0
    for eid, thread in gone:
        assert await _artifacts(db, eid, thread) == GONE
    for eid, thread in kept:
        assert await _artifacts(db, eid, thread) == FULL
        assert db.snapshot(await db.must(eid)) == before[eid]
