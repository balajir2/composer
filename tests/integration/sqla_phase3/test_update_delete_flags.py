"""Groups 5, 7, 8: update (PUT), delete, admin flags (PATCH admin-flags)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    LONG_AGO,
    Db,
    Member,
    WorkflowApi,
    chain,
    jbody,
    node,
    not_found,
    payload,
    simple_graph,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

SLUG_DETAIL = (
    "external_slug must be lowercase alphanumeric + hyphens, "
    "start with alphanumeric, and be 2-64 chars"
)
FORBIDDEN_EDIT = "Forbidden: not workflow owner or assignee."
FORBIDDEN_OWNER = "Forbidden: not workflow owner."


def slug() -> str:
    return unique("slug").lower()


def edit_body(created: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": created["name"],
        "nodes": created["nodes"],
        "edges": created["edges"],
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- 5. update


async def test_update_replaces_every_field_and_advances_updated_at(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    created = await wf_api.create(
        member,
        description="old",
        category="old-cat",
        tags=["old"],
        difficulty="hard",
        estimatedTime="1h",
        version="0",
        isTemplate=True,
        isPublic=True,
    )
    bystander = await db.seed(member)
    before_bystander = db.snapshot(await db.must(bystander))
    await db.backdate(created["id"])
    before = await db.must(created["id"])
    assert before.updated_at == LONG_AGO

    nodes, edges = chain(node("t", "transform", transformScript="x = 1"))
    resp = await wf_api.put(
        member,
        created["id"],
        {
            "name": "renamed",
            "description": "new",
            "category": "new-cat",
            "tags": ["x", "y"],
            "difficulty": "easy",
            "estimatedTime": "5m",
            "version": "2",
            "nodes": nodes,
            "edges": edges,
            "isTemplate": False,
            "isPublic": False,
        },
    )
    assert resp.status_code == 200, resp.text
    got = jbody(resp)
    assert got["id"] == created["id"] and got["userId"] == member.id
    assert (got["name"], got["description"], got["category"]) == ("renamed", "new", "new-cat")
    assert (got["tags"], got["difficulty"], got["estimatedTime"]) == (["x", "y"], "easy", "5m")
    assert (got["version"], got["isTemplate"], got["isPublic"]) == ("2", False, False)
    assert [n["id"] for n in got["nodes"]] == ["s", "t", "e"]
    assert got["createdAt"] == created["createdAt"]

    row = await db.must(created["id"])
    assert (
        row.updated_at > LONG_AGO and abs((datetime.now(UTC) - row.updated_at).total_seconds()) < 60
    )
    assert row.created_at == before.created_at and row.user_id == member.id
    assert row.tags == ["x", "y"]
    assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_update_omitted_optionals_are_reset(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    """PUT is a full replacement: optional fields left out of the body are cleared."""
    created = await wf_api.create(
        member,
        description="d",
        category="c",
        tags=["t"],
        difficulty="easy",
        estimatedTime="1m",
        version="1",
        isTemplate=True,
        isPublic=True,
    )
    resp = await wf_api.put(member, created["id"], edit_body(created))
    got = jbody(resp)
    assert resp.status_code == 200
    assert got["description"] is None and got["category"] is None and got["tags"] == []
    assert got["difficulty"] is None and got["estimatedTime"] is None and got["version"] is None
    assert got["isTemplate"] is False and got["isPublic"] is False
    assert (await db.must(created["id"])).tags == []


async def test_update_authorisation_owner_assignee_admin_allowed_others_403(
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    created = await wf_api.create(member)
    await db.assign(created["id"], second_member.id, member.id)
    bystander = await db.seed(third_member)
    before_bystander = db.snapshot(await db.must(bystander))

    for who in (member, second_member, admin):
        await db.backdate(created["id"])
        r = await wf_api.put(who, created["id"], edit_body(created, description=f"by {who.id}"))
        assert r.status_code == 200, (who, r.text)
        row = await db.must(created["id"])
        assert row.description == f"by {who.id}"
        assert row.user_id == member.id  # an assignee/admin edit never changes the owner
        assert row.updated_at > LONG_AGO

    await db.backdate(created["id"])
    frozen = db.snapshot(await db.must(created["id"]))
    r = await wf_api.put(third_member, created["id"], edit_body(created, description="hijack"))
    assert r.status_code == 403 and r.json()["detail"] == FORBIDDEN_EDIT
    assert db.snapshot(await db.must(created["id"])) == frozen
    assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_update_missing_is_404_and_authz_runs_before_shape_validation(
    client: AsyncClient, member: Member, second_member: Member, wf_api: WorkflowApi
) -> None:
    r = await client.put("/workflows/nope", json=payload(), headers=member.headers)
    assert r.status_code == 404 and r.json()["detail"] == not_found("nope")

    created = await wf_api.create(member)
    bad_shape = payload([node("only", "transform")], [])
    r = await wf_api.put(second_member, created["id"], bad_shape)
    assert r.status_code == 403  # 403 wins over the shape 422
    r = await wf_api.put(member, created["id"], bad_shape)
    assert r.status_code == 422
    assert r.json()["detail"] == "Workflow must contain exactly one start node (found: 0)."


async def test_update_rejections_leave_the_row_unchanged(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    created = await wf_api.create(member)
    await db.backdate(created["id"])
    frozen = db.snapshot(await db.must(created["id"]))

    mids = [node(f"n{i}", "transform") for i in range(99)]
    nodes, edges = chain(*mids)
    r = await wf_api.put(member, created["id"], payload(nodes, edges))
    assert r.status_code == 422 and r.json()["detail"] == "workflow exceeds max_nodes=100; got 101"
    r = await wf_api.put(member, created["id"], payload([node("x", "transform")], []))
    assert r.status_code == 422
    assert db.snapshot(await db.must(created["id"])) == frozen


async def test_update_collapses_duplicate_edges(member: Member, wf_api: WorkflowApi) -> None:
    created = await wf_api.create(member)
    nodes, _ = simple_graph()
    edges = [
        {"id": "keep", "source": "s", "target": "e"},
        {"id": "drop", "source": "s", "target": "e"},
    ]
    r = await wf_api.put(member, created["id"], payload(nodes, edges))
    assert r.status_code == 200 and [e["id"] for e in jbody(r)["edges"]] == ["keep"]


async def test_update_without_is_production_keeps_publication(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    created = await wf_api.create(member)
    s = slug()
    assert (await wf_api.publish(member, created, s)).status_code == 200
    r = await wf_api.put(member, created["id"], edit_body(created, description="edited"))
    got = jbody(r)
    assert r.status_code == 200 and got["isProduction"] is True and got["externalSlug"] == s
    row = await db.must(created["id"])
    assert row.is_production is True and row.external_slug == s


async def test_publish_and_unpublish(member: Member, wf_api: WorkflowApi, db: Db) -> None:
    created = await wf_api.create(member)
    s = slug()
    r = await wf_api.publish(member, created, s)
    got = jbody(r)
    assert r.status_code == 200 and got["isProduction"] is True and got["externalSlug"] == s

    # republish under a new slug
    s2 = slug()
    r = await wf_api.publish(member, got, s2)
    assert jbody(r)["externalSlug"] == s2

    # unpublish clears the slug (any slug in the body is ignored)
    r = await wf_api.put(
        member, created["id"], edit_body(created, isProduction=False, externalSlug="ignored-slug")
    )
    got = jbody(r)
    assert r.status_code == 200 and got["isProduction"] is False and got["externalSlug"] is None
    row = await db.must(created["id"])
    assert row.is_production is False and row.external_slug is None


async def test_publish_requires_a_valid_slug(member: Member, wf_api: WorkflowApi, db: Db) -> None:
    created = await wf_api.create(member)
    await db.backdate(created["id"])
    frozen = db.snapshot(await db.must(created["id"]))

    r = await wf_api.put(member, created["id"], edit_body(created, isProduction=True))
    assert r.status_code == 422
    assert r.json()["detail"] == "external_slug is required when isProduction=True"
    r = await wf_api.put(
        member, created["id"], edit_body(created, isProduction=True, externalSlug="")
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "external_slug is required when isProduction=True"

    for bad in ("A", "a", "Upper-Case", "has space", "-leads", "x" * 65, "under_score"):
        r = await wf_api.put(
            member, created["id"], edit_body(created, isProduction=True, externalSlug=bad)
        )
        assert r.status_code == 422 and r.json()["detail"] == SLUG_DETAIL, bad
    assert db.snapshot(await db.must(created["id"])) == frozen

    for ok in ("ab", "a" + "b" * 63, "0-1"):
        r = await wf_api.put(
            member, created["id"], edit_body(created, isProduction=True, externalSlug=ok)
        )
        assert r.status_code == 200, ok


async def test_publish_rejects_unknown_variable_references(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    nodes, edges = chain(node("t", "transform", transformScript="print('{{nope_var}}')"))
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    await db.backdate(created["id"])
    frozen = db.snapshot(await db.must(created["id"]))

    r = await wf_api.publish(member, created, slug())
    assert r.status_code == 422
    assert r.json()["detail"] == {
        "message": "Workflow references undeclared variables and cannot be published.",
        "problems": [{"nodeId": "t", "field": "transformScript", "placeholder": "nope_var"}],
    }
    assert db.snapshot(await db.must(created["id"])) == frozen

    # The same reference is fine on a plain save, and fine once it resolves to a known root
    r = await wf_api.put(member, created["id"], edit_body(created, description="plain save"))
    assert r.status_code == 200
    ok_nodes, ok_edges = chain(node("t", "transform", transformScript="print('{{lastOutput}}')"))
    r = await wf_api.put(
        member,
        created["id"],
        payload(ok_nodes, ok_edges, isProduction=True, externalSlug=slug()),
    )
    assert r.status_code == 200 and jbody(r)["isProduction"] is True


async def test_publish_slug_conflict_is_409_by_prefix(
    member: Member, second_member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    holder = await wf_api.create(second_member)
    taken = slug()
    assert (await wf_api.publish(second_member, holder, taken)).status_code == 200
    holder_before = db.snapshot(await db.must(holder["id"]))

    mine = await wf_api.create(member)
    await db.backdate(mine["id"])
    mine_before = db.snapshot(await db.must(mine["id"]))
    r = await wf_api.publish(member, mine, taken)
    assert r.status_code == 409
    assert r.json()["detail"].startswith("external_slug already in use: ")
    assert db.snapshot(await db.must(mine["id"])) == mine_before
    assert db.snapshot(await db.must(holder["id"])) == holder_before

    # re-publishing the holder under its own slug is not a conflict
    assert (await wf_api.publish(second_member, holder, taken)).status_code == 200


# --------------------------------------------------------------------------- 7. delete


async def test_delete_by_owner_and_admin_cascades_assignments_and_executions(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    db: Db,
) -> None:
    bystander = await db.seed(member)
    await db.assign(bystander, third_member.id, member.id)
    bystander_exec = await db.seed_execution(bystander, member.id)
    before = db.snapshot(await db.must(bystander))

    for deleter in (member, admin):
        wid = await db.seed(member)
        await db.assign(wid, second_member.id, member.id)
        await db.assign(wid, third_member.id, member.id)
        exec_id = await db.seed_execution(wid, member.id)
        assert len(await db.assignments(wid)) == 2

        r = await client.delete(f"/workflows/{wid}", headers=deleter.headers)
        assert r.status_code == 204 and r.content == b""
        assert await db.workflow(wid) is None
        assert await db.assignments(wid) == []
        assert not await db.execution_exists(exec_id)
        assert (await client.get(f"/workflows/{wid}", headers=admin.headers)).status_code == 404

    assert db.snapshot(await db.must(bystander)) == before
    assert len(await db.assignments(bystander)) == 1
    assert await db.execution_exists(bystander_exec)


async def test_delete_forbidden_for_assignee_and_strangers(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    db: Db,
) -> None:
    wid = await db.seed(member, is_public=True)
    await db.assign(wid, second_member.id, member.id)
    frozen = db.snapshot(await db.must(wid))
    for who in (second_member, third_member):  # assignee and (public-viewing) stranger alike
        r = await client.delete(f"/workflows/{wid}", headers=who.headers)
        assert r.status_code == 403 and r.json()["detail"] == FORBIDDEN_OWNER
    assert db.snapshot(await db.must(wid)) == frozen
    assert len(await db.assignments(wid)) == 1


async def test_delete_missing_is_404(client: AsyncClient, member: Member) -> None:
    r = await client.delete("/workflows/nope", headers=member.headers)
    assert r.status_code == 404 and r.json()["detail"] == not_found("nope")


# --------------------------------------------------------------------------- 8. admin flags


async def test_admin_flags_admin_only(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed(member)
    frozen = db.snapshot(await db.must(wid))
    r = await client.patch(
        f"/workflows/{wid}/admin-flags", json={"isPublic": True}, headers=member.headers
    )
    assert r.status_code == 403
    assert db.snapshot(await db.must(wid)) == frozen
    r = await client.patch(f"/workflows/{wid}/admin-flags", json={"isPublic": True})
    assert r.status_code in (401, 403)  # dev-mode fallback user is not an admin either


async def test_admin_flags_missing_is_404(client: AsyncClient, admin: Member) -> None:
    r = await client.patch(
        "/workflows/nope/admin-flags", json={"isPublic": True}, headers=admin.headers
    )
    assert r.status_code == 404 and r.json()["detail"] == not_found("nope")


async def test_admin_flags_set_public_and_updated_at_advances(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed(member)
    bystander = await db.seed(member)
    before_bystander = db.snapshot(await db.must(bystander))
    await db.backdate(wid)
    r = await client.patch(
        f"/workflows/{wid}/admin-flags", json={"isPublic": True}, headers=admin.headers
    )
    got = jbody(r)
    assert r.status_code == 200 and got["isPublic"] is True and got["userId"] == member.id
    row = await db.must(wid)
    assert row.is_public is True and row.updated_at > LONG_AGO
    assert db.snapshot(await db.must(bystander)) == before_bystander
    r = await client.patch(
        f"/workflows/{wid}/admin-flags", json={"isPublic": False}, headers=admin.headers
    )
    assert jbody(r)["isPublic"] is False


async def test_admin_flags_empty_payload_returns_row_without_writing(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed(member, name="untouched")
    await db.backdate(wid)
    frozen = db.snapshot(await db.must(wid))
    r = await client.patch(f"/workflows/{wid}/admin-flags", json={}, headers=admin.headers)
    got = jbody(r)
    assert r.status_code == 200 and got["id"] == wid and got["name"] == "untouched"
    assert db.snapshot(await db.must(wid)) == frozen  # updated_at still LONG_AGO
    # explicit nulls are the same as absent
    r = await client.patch(
        f"/workflows/{wid}/admin-flags",
        json={"isPublic": None, "isProduction": None, "externalSlug": None},
        headers=admin.headers,
    )
    assert r.status_code == 200 and db.snapshot(await db.must(wid)) == frozen


async def test_admin_flags_publish_unpublish_and_slug_change(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed(member)
    url = f"/workflows/{wid}/admin-flags"
    r = await client.patch(url, json={"isProduction": True}, headers=admin.headers)
    assert r.status_code == 422
    assert r.json()["detail"] == "external_slug is required when isProduction=True"
    r = await client.patch(
        url, json={"isProduction": True, "externalSlug": "Bad Slug"}, headers=admin.headers
    )
    assert r.status_code == 422 and r.json()["detail"] == SLUG_DETAIL

    s = slug()
    r = await client.patch(
        url, json={"isProduction": True, "externalSlug": s}, headers=admin.headers
    )
    got = jbody(r)
    assert r.status_code == 200 and got["isProduction"] is True and got["externalSlug"] == s

    # republish with no slug in the body reuses the stored slug
    r = await client.patch(url, json={"isProduction": True}, headers=admin.headers)
    assert jbody(r)["externalSlug"] == s

    # slug change without touching isProduction
    s2 = slug()
    r = await client.patch(url, json={"externalSlug": s2}, headers=admin.headers)
    got = jbody(r)
    assert got["externalSlug"] == s2 and got["isProduction"] is True
    r = await client.patch(url, json={"externalSlug": "Bad Slug"}, headers=admin.headers)
    assert r.status_code == 422 and r.json()["detail"] == SLUG_DETAIL

    r = await client.patch(url, json={"isProduction": False}, headers=admin.headers)
    got = jbody(r)
    assert got["isProduction"] is False and got["externalSlug"] is None
    row = await db.must(wid)
    assert row.is_production is False and row.external_slug is None


async def test_admin_flags_publish_rejects_unknown_variable_references(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    nodes, edges = chain(node("t", "transform", transformScript="print('{{nope_var}}')"))
    wid = await db.seed(member, nodes=nodes, edges=edges)
    frozen = db.snapshot(await db.must(wid))
    r = await client.patch(
        f"/workflows/{wid}/admin-flags",
        json={"isProduction": True, "externalSlug": slug()},
        headers=admin.headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["problems"] == [
        {"nodeId": "t", "field": "transformScript", "placeholder": "nope_var"}
    ]
    assert db.snapshot(await db.must(wid)) == frozen


async def test_admin_flags_slug_conflict_is_409_by_prefix(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    taken = slug()
    holder = await db.seed(member, is_production=True, external_slug=taken)
    other = await db.seed(member)
    holder_before = db.snapshot(await db.must(holder))
    other_before = db.snapshot(await db.must(other))

    for body in ({"isProduction": True, "externalSlug": taken}, {"externalSlug": taken}):
        r = await client.patch(f"/workflows/{other}/admin-flags", json=body, headers=admin.headers)
        assert r.status_code == 409, body
        assert r.json()["detail"].startswith("external_slug already in use: ")
    assert db.snapshot(await db.must(other)) == other_before
    assert db.snapshot(await db.must(holder)) == holder_before


async def test_admin_flags_do_not_touch_other_columns(
    client: AsyncClient, member: Member, admin: Member, db: Db
) -> None:
    wid = await db.seed(member, description="d", category="c", tags=["t"], is_template=True)
    before = db.snapshot(await db.must(wid))
    r = await client.patch(
        f"/workflows/{wid}/admin-flags", json={"isPublic": True}, headers=admin.headers
    )
    assert r.status_code == 200
    after = db.snapshot(await db.must(wid))
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"is_public", "updated_at"}
