"""Group 3: GET /executions and GET /executions/{id}."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    EXECUTION_KEYS,
    HTTPX_MARK,
    ISO,
    Db,
    Member,
    ago,
    jbody,
    not_found,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

# Offsets in hours, deliberately NOT in sorted order, so a wrong ORDER BY (or none) fails.
OFFSETS_H = [2, 5, 1, 4, 3]


async def _seed_five(
    db: Db, wid: str, owner: Member, status_by_offset: dict[int, str]
) -> dict[int, str]:
    ids: dict[int, str] = {}
    for h in OFFSETS_H:
        ids[h] = await db.seed_execution(
            wid, owner, status=status_by_offset.get(h, "completed"), started_at=ago(hours=h)
        )
    return ids


async def test_list_member_sees_own_only_ordered_desc_with_exact_shape(
    client: AsyncClient, member: Member, second_member: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member, is_public=True)
    mine = await _seed_five(db, wid, member, {1: "running", 2: "failed"})
    theirs = await db.seed_execution(wid, second_member, started_at=ago(minutes=1))
    nobody = await db.seed_execution(wid, None, started_at=ago(minutes=2))

    r = await client.get("/executions", headers=member.headers)
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == {"total", "items", "limit", "offset"}
    assert body["total"] == 5 and body["limit"] == 50 and body["offset"] == 0
    assert [i["id"] for i in body["items"]] == [mine[h] for h in sorted(OFFSETS_H)]
    assert all(set(i) == EXECUTION_KEYS for i in body["items"])
    assert theirs not in {i["id"] for i in body["items"]}
    assert nobody not in {i["id"] for i in body["items"]}
    first = body["items"][0]
    assert first["status"] == "running" and first["userId"] == member.id
    assert ISO.match(first["startedAt"]) and first["workflowId"] == wid


async def test_list_admin_sees_all_users_rows_of_a_workflow(
    client: AsyncClient, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member, is_public=True)
    mine = await _seed_five(db, wid, member, {})
    theirs = await db.seed_execution(wid, second_member, started_at=ago(minutes=1))
    nobody = await db.seed_execution(wid, None, started_at=ago(hours=9))

    r = await client.get("/executions", params={"workflowId": wid}, headers=admin.headers)
    assert r.status_code == 200
    body = jbody(r)
    assert body["total"] == 7
    expected = [theirs] + [mine[h] for h in sorted(OFFSETS_H)] + [nobody]
    assert [i["id"] for i in body["items"]] == expected
    # the same query as a member excludes the other users' rows and the anonymous one
    r2 = await client.get("/executions", params={"workflowId": wid}, headers=member.headers)
    assert jbody(r2)["total"] == 5


async def test_list_filters_limit_offset_total(client: AsyncClient, member: Member, db: Db) -> None:
    w1 = await db.seed_workflow(member)
    w2 = await db.seed_workflow(member)
    ids = await _seed_five(db, w1, member, {1: "failed", 3: "failed", 4: "running"})
    other_wf_exec = await db.seed_execution(w2, member, status="failed", started_at=ago(minutes=3))
    order = [ids[h] for h in sorted(OFFSETS_H)]  # newest first

    async def lst(**params: Any) -> dict[str, Any]:
        r = await client.get("/executions", params=params, headers=member.headers)
        assert r.status_code == 200, r.text
        return jbody(r)

    page = await lst(workflowId=w1, limit=2, offset=1)
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 1
    assert [i["id"] for i in page["items"]] == order[1:3]
    tail = await lst(workflowId=w1, limit=2, offset=4)
    assert [i["id"] for i in tail["items"]] == order[4:] and tail["total"] == 5
    beyond = await lst(workflowId=w1, offset=50)
    assert beyond["items"] == [] and beyond["total"] == 5

    failed = await lst(workflowId=w1, status="failed")
    assert failed["total"] == 2
    assert [i["id"] for i in failed["items"]] == [ids[1], ids[3]]
    only_w2 = await lst(workflowId=w2)
    assert [i["id"] for i in only_w2["items"]] == [other_wf_exec]
    all_failed = await lst(status="failed")  # across workflows, still own only
    assert all_failed["total"] == 3
    assert [i["id"] for i in all_failed["items"]] == [other_wf_exec, ids[1], ids[3]]
    unknown = await lst(status="no-such-status")
    assert unknown == {"total": 0, "items": [], "limit": 50, "offset": 0}
    assert (await lst(workflowId="no-such-wf"))["total"] == 0


@pytest.mark.parametrize("params", [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"limit": "abc"}])
async def test_list_rejects_bad_paging(
    client: AsyncClient, member: Member, params: dict[str, Any]
) -> None:
    r = await client.get("/executions", params=params, headers=member.headers)
    assert r.status_code == 422, r.text


async def test_list_limit_100_is_allowed(client: AsyncClient, member: Member) -> None:
    r = await client.get("/executions", params={"limit": 100}, headers=member.headers)
    assert r.status_code == 200 and jbody(r)["limit"] == 100


async def test_get_owner_admin_other_and_missing(
    client: AsyncClient, member: Member, second_member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    completed = ago(minutes=3)
    eid = await db.seed_execution(
        wid,
        member,
        status="failed",
        error="Boom: it failed",
        failure_code="execution_error",
        completed_at=completed,
        current_node_id="n1",
        node_results={"a": {"b": 1}},
        variables={"k": "v"},
        input={"i": 1},
        output=[1, 2],
    )
    bystander = await db.seed_execution(wid, second_member)
    before = db.snapshot(await db.must(bystander))

    for who in (member, admin):
        r = await client.get(f"/executions/{eid}", headers=who.headers)
        assert r.status_code == 200, r.text
        body = jbody(r)
        assert set(body) == EXECUTION_KEYS
        assert body["id"] == eid and body["workflowId"] == wid and body["userId"] == member.id
        assert body["status"] == "failed" and body["error"] == "Boom: it failed"
        assert body["failureCode"] == "execution_error" and body["currentNodeId"] == "n1"
        assert body["nodeResults"] == {"a": {"b": 1}} and body["variables"] == {"k": "v"}
        assert body["input"] == {"i": 1} and body["output"] == [1, 2]
        assert ISO.match(body["startedAt"]) and ISO.match(body["completedAt"])
    for who in (second_member,):
        r = await client.get(f"/executions/{eid}", headers=who.headers)
        assert r.status_code == 404 and jbody(r) == {"detail": not_found(eid)}
    r = await client.get("/executions/does-not-exist", headers=member.headers)
    assert r.status_code == 404 and jbody(r) == {"detail": not_found("does-not-exist")}
    assert db.snapshot(await db.must(bystander)) == before


async def test_get_anonymous_row_is_admin_only(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed_workflow(member)
    eid = await db.seed_execution(wid, None)
    assert (await client.get(f"/executions/{eid}", headers=member.headers)).status_code == 404
    assert (await client.get(f"/executions/{eid}", headers=admin.headers)).status_code == 200
