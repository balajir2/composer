"""Groups 9, 10, 14: owner assignment, assignments, e2e cleanup."""

import secrets

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    ISO,
    LONG_AGO,
    Db,
    MakeUser,
    Member,
    WorkflowApi,
    jbody,
    jlist,
    not_found,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

FORBIDDEN_OWNER = "Forbidden: not workflow owner."


# --------------------------------------------------------------------------- 9. owner assignment


async def test_owner_assignment_admin_only(
    client: AsyncClient, member: Member, second_member: Member, db: Db
) -> None:
    wid = await db.seed(member)
    frozen = db.snapshot(await db.must(wid))
    for who in (member, second_member):
        r = await client.patch(
            f"/workflows/{wid}/owner", json={"userId": second_member.id}, headers=who.headers
        )
        assert r.status_code == 403
    assert db.snapshot(await db.must(wid)) == frozen


async def test_owner_assignment_by_id_changes_only_owner_and_updated_at(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    wid = await db.seed(
        member, description="d", category="c", tags=["t"], is_public=False, is_template=True
    )
    await db.assign(wid, third_member.id, member.id)
    await db.backdate(wid)
    bystander = await db.seed(member)
    before_bystander = db.snapshot(await db.must(bystander))
    before = db.snapshot(await db.must(wid))

    r = await client.patch(
        f"/workflows/{wid}/owner", json={"userId": second_member.id}, headers=admin.headers
    )
    assert r.status_code == 200, r.text
    assert jbody(r)["userId"] == second_member.id and jbody(r)["id"] == wid
    after = db.snapshot(await db.must(wid))
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"user_id", "updated_at"} and after["updated_at"] > LONG_AGO
    assert db.snapshot(await db.must(bystander)) == before_bystander
    # assignments survive an owner change
    assert [a.user_id for a in await db.assignments(wid)] == [third_member.id]

    # visibility follows the new owner
    assert (await wf_api.get(second_member, wid)).status_code == 200
    assert (await wf_api.get(member, wid)).status_code == 404  # private, no longer owner
    assert (await wf_api.get(third_member, wid)).status_code == 200  # still assigned
    r = await client.delete(f"/workflows/{wid}", headers=member.headers)
    assert r.status_code == 403


async def test_owner_assignment_by_email_is_lowercased(
    client: AsyncClient, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed(member)
    r = await client.patch(
        f"/workflows/{wid}/owner",
        json={"email": second_member.email.upper()},
        headers=admin.headers,
    )
    assert r.status_code == 200 and jbody(r)["userId"] == second_member.id
    assert (await db.must(wid)).user_id == second_member.id


async def test_owner_assignment_user_id_wins_over_email(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    db: Db,
) -> None:
    wid = await db.seed(member)
    r = await client.patch(
        f"/workflows/{wid}/owner",
        json={"userId": second_member.id, "email": third_member.email},
        headers=admin.headers,
    )
    assert r.status_code == 200 and jbody(r)["userId"] == second_member.id


async def test_owner_assignment_errors(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed(member)
    frozen = db.snapshot(await db.must(wid))
    url = f"/workflows/{wid}/owner"

    r = await client.patch(url, json={}, headers=admin.headers)
    assert r.status_code == 422 and r.json()["detail"] == "must provide user_id or email"
    r = await client.patch(url, json={"email": ""}, headers=admin.headers)
    assert r.status_code == 422 and r.json()["detail"] == "must provide user_id or email"

    r = await client.patch(url, json={"userId": "no-such-user"}, headers=admin.headers)
    assert r.status_code == 404 and r.json()["detail"] == "user 'no-such-user' not found"
    r = await client.patch(url, json={"email": "Nobody@Example.com"}, headers=admin.headers)
    assert r.status_code == 404
    assert r.json()["detail"] == "user with email 'Nobody@Example.com' not found"

    # the user is resolved before the workflow: both missing reports the user
    r = await client.patch(
        "/workflows/nope/owner", json={"userId": "no-such-user"}, headers=admin.headers
    )
    assert r.status_code == 404 and r.json()["detail"] == "user 'no-such-user' not found"
    r = await client.patch(
        "/workflows/nope/owner", json={"userId": member.id}, headers=admin.headers
    )
    assert r.status_code == 404 and r.json()["detail"] == not_found("nope")
    assert db.snapshot(await db.must(wid)) == frozen


# --------------------------------------------------------------------------- 10. assignments


async def test_assignments_list_owner_and_admin_only(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    db: Db,
) -> None:
    wid = await db.seed(member)
    other = await db.seed(member)
    await db.assign(wid, second_member.id, member.id)
    await db.assign(wid, third_member.id, admin.id)
    await db.assign(other, second_member.id, member.id)

    for who in (member, admin):
        r = await client.get(f"/workflows/{wid}/assignments", headers=who.headers)
        assert r.status_code == 200, r.text
        rows = jlist(r)
        assert {(a["userId"], a["assignedById"]) for a in rows} == {
            (second_member.id, member.id),
            (third_member.id, admin.id),
        }
        for a in rows:
            assert set(a) == {"id", "workflowId", "userId", "assignedById", "assignedAt"}
            assert a["workflowId"] == wid and ISO.match(a["assignedAt"])

    for who in (second_member, third_member):  # assignees may not list
        r = await client.get(f"/workflows/{wid}/assignments", headers=who.headers)
        assert r.status_code == 403 and r.json()["detail"] == FORBIDDEN_OWNER
    r = await client.get("/workflows/nope/assignments", headers=member.headers)
    assert r.status_code == 404 and r.json()["detail"] == not_found("nope")

    empty = await db.seed(member)
    r = await client.get(f"/workflows/{empty}/assignments", headers=member.headers)
    assert r.status_code == 200 and r.json() == []


async def test_assignment_grant_by_owner_and_admin(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    wid = await db.seed(member)
    bystander = await db.seed(member)
    before_bystander = db.snapshot(await db.must(bystander))
    frozen_wf = db.snapshot(await db.must(wid))

    r = await client.post(
        f"/workflows/{wid}/assignments/{second_member.id}", headers=member.headers
    )
    got = jbody(r)
    assert r.status_code == 201, r.text
    assert set(got) == {"id", "workflowId", "userId", "assignedById", "assignedAt"}
    assert got["workflowId"] == wid and got["userId"] == second_member.id
    assert got["assignedById"] == member.id and ISO.match(got["assignedAt"])

    r = await client.post(f"/workflows/{wid}/assignments/{third_member.id}", headers=admin.headers)
    assert (
        r.status_code == 201 and jbody(r)["assignedById"] == admin.id
    )  # the caller, not the owner

    rows = await db.assignments(wid)
    assert {(a.user_id, a.assigned_by_id) for a in rows} == {
        (second_member.id, member.id),
        (third_member.id, admin.id),
    }
    assert db.snapshot(await db.must(wid)) == frozen_wf  # the workflow row is not touched
    assert db.snapshot(await db.must(bystander)) == before_bystander
    assert await db.assignments(bystander) == []

    # an assignee can open and edit but not delete or share
    assert (await wf_api.get(second_member, wid)).status_code == 200
    r = await client.post(
        f"/workflows/{wid}/assignments/{member.id}", headers=second_member.headers
    )
    assert r.status_code == 403 and r.json()["detail"] == FORBIDDEN_OWNER
    r = await client.delete(f"/workflows/{wid}", headers=second_member.headers)
    assert r.status_code == 403


async def test_assignment_grant_to_the_owner_is_allowed(
    client: AsyncClient, member: Member, db: Db
) -> None:
    wid = await db.seed(member)
    r = await client.post(f"/workflows/{wid}/assignments/{member.id}", headers=member.headers)
    assert r.status_code == 201
    assert [(a.user_id, a.assigned_by_id) for a in await db.assignments(wid)] == [
        (member.id, member.id)
    ]


async def test_assignment_grant_errors(
    client: AsyncClient, member: Member, second_member: Member, third_member: Member, db: Db
) -> None:
    wid = await db.seed(member)
    r = await client.post(
        f"/workflows/{wid}/assignments/{second_member.id}", headers=member.headers
    )
    assert r.status_code == 201
    first = (await db.assignments(wid))[0]

    r = await client.post(
        f"/workflows/{wid}/assignments/{second_member.id}", headers=member.headers
    )
    assert r.status_code == 409
    assert r.json()["detail"].startswith(
        f"assignment already exists for user '{second_member.id}': "
    )
    rows = await db.assignments(wid)
    assert len(rows) == 1 and rows[0].id == first.id and rows[0].assigned_at == first.assigned_at

    r = await client.post(f"/workflows/{wid}/assignments/no-such-user", headers=member.headers)
    assert r.status_code == 404 and r.json()["detail"] == "user 'no-such-user' not found"
    r = await client.post(f"/workflows/nope/assignments/{second_member.id}", headers=member.headers)
    assert r.status_code == 404 and r.json()["detail"] == not_found("nope")
    # the workflow is checked first, then permission, then the target user
    r = await client.post("/workflows/nope/assignments/no-such-user", headers=member.headers)
    assert r.json()["detail"] == not_found("nope")
    r = await client.post(
        f"/workflows/{wid}/assignments/no-such-user", headers=third_member.headers
    )
    assert r.status_code == 403 and r.json()["detail"] == FORBIDDEN_OWNER
    assert len(await db.assignments(wid)) == 1


async def test_assignment_revoke(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    db: Db,
) -> None:
    wid = await db.seed(member)
    other = await db.seed(member)
    await db.assign(wid, second_member.id, member.id)
    await db.assign(wid, third_member.id, member.id)
    await db.assign(other, second_member.id, member.id)
    before_other = db.snapshot(await db.must(other))

    r = await client.delete(
        f"/workflows/{wid}/assignments/{second_member.id}", headers=member.headers
    )
    assert r.status_code == 204 and r.content == b""
    assert [a.user_id for a in await db.assignments(wid)] == [third_member.id]
    r = await client.delete(
        f"/workflows/{wid}/assignments/{third_member.id}", headers=admin.headers
    )
    assert r.status_code == 204
    assert await db.assignments(wid) == []
    # a different workflow's assignment for the same user is untouched
    assert [a.user_id for a in await db.assignments(other)] == [second_member.id]
    assert db.snapshot(await db.must(other)) == before_other

    # not owner/admin -> 403; unknown workflow -> 404
    await db.assign(wid, second_member.id, member.id)
    r = await client.delete(
        f"/workflows/{wid}/assignments/{second_member.id}", headers=second_member.headers
    )
    assert r.status_code == 403 and r.json()["detail"] == FORBIDDEN_OWNER
    assert len(await db.assignments(wid)) == 1
    r = await client.delete(
        f"/workflows/nope/assignments/{second_member.id}", headers=member.headers
    )
    assert r.status_code == 404 and r.json()["detail"] == not_found("nope")


async def test_revoke_missing_assignment_today_404(
    client: AsyncClient, member: Member, second_member: Member, db: Db
) -> None:
    """PINNED (differs from the plan, which expected 500): Prisma Python's delete() returns None
    for a missing row (it does not raise), so the route's `deleted is None` branch IS reachable
    and the client gets 404 "assignment not found for user '<id>'". Task 3 must keep this, not
    "fix" it; no test needs updating for the revoke-missing case."""
    wid = await db.seed(member)
    bystander = await db.seed(member)
    await db.assign(bystander, second_member.id, member.id)
    before = [(a.id, a.user_id) for a in await db.assignments(bystander)]
    r = await client.delete(
        f"/workflows/{wid}/assignments/{second_member.id}", headers=member.headers
    )
    assert r.status_code == 404
    assert r.json()["detail"] == f"assignment not found for user '{second_member.id}'"
    # revoking twice behaves the same, and nothing else is touched
    await db.assign(wid, second_member.id, member.id)
    assert (
        await client.delete(
            f"/workflows/{wid}/assignments/{second_member.id}", headers=member.headers
        )
    ).status_code == 204
    r = await client.delete(
        f"/workflows/{wid}/assignments/{second_member.id}", headers=member.headers
    )
    assert r.status_code == 404
    assert [(a.id, a.user_id) for a in await db.assignments(bystander)] == before


# --------------------------------------------------------------------------- 14. cleanup


async def test_cleanup_removes_workflows_and_all_assignments_of_the_user(
    client: AsyncClient,
    make_user: MakeUser,
    member: Member,
    second_member: Member,
    db: Db,
) -> None:
    e2e = await make_user(email=f"pw-{secrets.token_hex(5)}@example.com")
    mine_a = await db.seed(e2e)
    mine_b = await db.seed(e2e, is_public=True)
    await db.assign(mine_a, member.id, e2e.id)  # e2e as assigner, on its own workflow
    mine_exec = await db.seed_execution(mine_a, e2e.id)

    others = await db.seed(member)
    await db.assign(others, e2e.id, member.id)  # e2e as assignee on someone else's workflow
    await db.assign(others, second_member.id, e2e.id)  # e2e as assigner on someone else's workflow
    keep_assignment = await db.assign(others, member.id, member.id)  # unrelated, same workflow
    bystander = await db.seed(second_member)
    await db.assign(bystander, member.id, second_member.id)
    before_others = db.snapshot(await db.must(others))
    before_bystander = db.snapshot(await db.must(bystander))

    r = await client.delete("/internal/test-users/me", headers=e2e.headers)
    assert r.status_code == 204 and r.content == b""

    assert await db.workflow(mine_a) is None and await db.workflow(mine_b) is None
    assert await db.assignments(mine_a) == []
    assert not await db.execution_exists(mine_exec)
    assert await db.assignments_for_user(e2e.id) == []
    assert not await db.user_exists(e2e.id)

    # someone else's workflow stays, with only its unrelated assignment
    assert db.snapshot(await db.must(others)) == before_others
    assert [a.id for a in await db.assignments(others)] == [keep_assignment]
    assert db.snapshot(await db.must(bystander)) == before_bystander
    assert len(await db.assignments(bystander)) == 1


async def test_cleanup_refuses_non_test_accounts_and_removes_nothing(
    client: AsyncClient, member: Member, db: Db
) -> None:
    wid = await db.seed(member)
    await db.assign(wid, member.id, member.id)
    frozen = db.snapshot(await db.must(wid))
    r = await client.delete("/internal/test-users/me", headers=member.headers)
    assert r.status_code == 403
    assert r.json()["detail"] == (
        "this endpoint only deletes accounts matching the e2e test-user pattern"
    )
    assert db.snapshot(await db.must(wid)) == frozen
    assert len(await db.assignments(wid)) == 1
    assert await db.user_exists(member.id)
