"""Groups 1-2: create and get-by-id (visibility)."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    ISO,
    Db,
    Member,
    WorkflowApi,
    chain,
    edge,
    iso_dt,
    jbody,
    node,
    not_found,
    payload,
    simple_graph,
    start_node,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


# --------------------------------------------------------------------------- 1. create


async def test_create_full_payload_round_trips(client: AsyncClient, member: Member, db: Db) -> None:
    nodes, edges = chain(node("t", "transform", transformExpression="1"))
    body = {
        "name": "Full",
        "description": "d",
        "category": "cat-x",
        "tags": ["a", "b"],
        "difficulty": "easy",
        "estimatedTime": "5m",
        "nodes": nodes,
        "edges": edges,
        "version": "1.2.3",
        "isTemplate": True,
        "isPublic": True,
    }
    resp = await client.post("/workflows", json=body, headers=member.headers)
    assert resp.status_code == 201, resp.text
    got = jbody(resp)
    assert got["userId"] == member.id and got["id"]
    assert (got["name"], got["description"], got["category"]) == ("Full", "d", "cat-x")
    assert (got["tags"], got["difficulty"], got["estimatedTime"]) == (["a", "b"], "easy", "5m")
    assert (got["version"], got["isTemplate"], got["isPublic"]) == ("1.2.3", True, True)
    assert got["isProduction"] is False and got["externalSlug"] is None
    assert ISO.match(got["createdAt"]) and ISO.match(got["updatedAt"])
    assert [n["id"] for n in got["nodes"]] == ["s", "t", "e"]
    assert [n["type"] for n in got["nodes"]] == ["start", "transform", "end"]
    assert got["nodes"][1]["position"] == {"x": 200, "y": 0}
    assert got["nodes"][1]["data"]["label"] == "t"
    assert [(e["id"], e["source"], e["target"]) for e in got["edges"]] == [
        ("s-t", "s", "t"),
        ("t-e", "t", "e"),
    ]

    row = await db.must(got["id"])
    assert row.user_id == member.id
    assert row.tags == ["a", "b"] and row.is_template and row.is_public
    assert [n["id"] for n in row.nodes] == ["s", "t", "e"]

    again = jbody(await client.get(f"/workflows/{got['id']}", headers=member.headers))
    assert again == got


async def test_create_ignores_is_production_and_external_slug(
    client: AsyncClient, member: Member, db: Db
) -> None:
    resp = await client.post(
        "/workflows",
        json=payload(isProduction=True, externalSlug="should-be-ignored"),
        headers=member.headers,
    )
    assert resp.status_code == 201, resp.text
    got = jbody(resp)
    assert got["isProduction"] is False and got["externalSlug"] is None
    row = await db.must(got["id"])
    assert row.is_production is False and row.external_slug is None


async def test_create_defaults_when_optional_fields_omitted(
    client: AsyncClient, member: Member, db: Db
) -> None:
    resp = await client.post("/workflows", json=payload(), headers=member.headers)
    got = jbody(resp)
    assert resp.status_code == 201
    assert got["description"] is None and got["category"] is None and got["tags"] == []
    assert got["difficulty"] is None and got["estimatedTime"] is None and got["version"] is None
    assert got["isTemplate"] is False and got["isPublic"] is False
    row = await db.must(got["id"])
    assert row.tags == []


async def test_create_collapses_duplicate_edges_keeping_first(
    client: AsyncClient, member: Member
) -> None:
    nodes, _ = simple_graph()
    edges = [edge("s", "e", "first"), edge("s", "e", "second"), edge("s", "e", "third")]
    resp = await client.post("/workflows", json=payload(nodes, edges), headers=member.headers)
    assert resp.status_code == 201, resp.text
    assert [e["id"] for e in jbody(resp)["edges"]] == ["first"]


async def test_create_node_limit(client: AsyncClient, member: Member) -> None:
    mids = [node(f"n{i}", "transform", transformExpression="1") for i in range(99)]
    nodes, edges = chain(*mids)  # 101 nodes
    assert len(nodes) == 101
    resp = await client.post("/workflows", json=payload(nodes, edges), headers=member.headers)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "workflow exceeds max_nodes=100; got 101"


async def test_create_exactly_at_node_limit_is_accepted(
    client: AsyncClient, member: Member
) -> None:
    mids = [node(f"n{i}", "transform", transformExpression="1") for i in range(98)]
    nodes, edges = chain(*mids)
    assert len(nodes) == 100
    resp = await client.post("/workflows", json=payload(nodes, edges), headers=member.headers)
    assert resp.status_code == 201, resp.text


async def test_create_edge_limit(client: AsyncClient, member: Member) -> None:
    # 30 chained nodes plus every forward skip edge: more than 200 distinct edges.
    mids = [node(f"n{i}", "transform", transformExpression="1") for i in range(28)]
    nodes, _ = chain(*mids)
    ids = [n["id"] for n in mids]
    edges = [edge(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
    edges += [edge("s", ids[0]), edge(ids[-1], "e")]
    assert len(edges) > 200
    resp = await client.post("/workflows", json=payload(nodes, edges), headers=member.headers)
    assert resp.status_code == 422
    assert resp.json()["detail"] == f"workflow exceeds max_edges=200; got {len(edges)}"


@pytest.mark.parametrize(
    ("nodes", "edges", "detail"),
    [
        (
            [node("only", "transform", transformExpression="1")],
            [],
            "Workflow must contain exactly one start node (found: 0).",
        ),
        (
            [start_node("s1"), start_node("s2")],
            [],
            "Workflow must contain exactly one start node (found: 2).",
        ),
    ],
)
async def test_create_shape_violation_is_422_with_message(
    client: AsyncClient,
    member: Member,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    detail: str,
) -> None:
    resp = await client.post("/workflows", json=payload(nodes, edges), headers=member.headers)
    assert resp.status_code == 422
    assert resp.json()["detail"] == detail


async def test_create_invalid_body_shapes(client: AsyncClient, member: Member) -> None:
    nodes, edges = simple_graph()
    # missing name
    r = await client.post(
        "/workflows", json={"nodes": nodes, "edges": edges}, headers=member.headers
    )
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "name"]
    # unknown node type
    bad = [*nodes, {"id": "x", "type": "nope", "position": {"x": 0, "y": 0}, "data": {}}]
    r = await client.post("/workflows", json=payload(bad, edges), headers=member.headers)
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    # missing nodes
    r = await client.post("/workflows", json={"name": "x", "edges": []}, headers=member.headers)
    assert r.status_code == 422


# --------------------------------------------------------------------------- 2. get visibility


async def test_get_visibility_matrix(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    private = await wf_api.create(member)
    public = await wf_api.create(member, isPublic=True)
    await db.assign(private["id"], second_member.id, member.id)

    for who in (member, second_member, admin):
        r = await wf_api.get(who, private["id"])
        assert r.status_code == 200 and jbody(r)["id"] == private["id"], who
    # assignee of the private one is NOT assigned to the public one; public is open to everyone
    for who in (member, second_member, third_member, admin):
        r = await wf_api.get(who, public["id"])
        assert r.status_code == 200 and jbody(r)["id"] == public["id"]

    r = await wf_api.get(third_member, private["id"])
    assert r.status_code == 404
    assert r.json()["detail"] == not_found(private["id"])


async def test_get_missing_is_404_with_same_detail_as_hidden(
    client: AsyncClient, member: Member, wf_api: WorkflowApi, second_member: Member
) -> None:
    missing = await client.get("/workflows/does-not-exist", headers=member.headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == not_found("does-not-exist")
    hidden = await wf_api.create(member)
    r = await wf_api.get(second_member, hidden["id"])
    assert r.status_code == 404 and r.json()["detail"] == not_found(hidden["id"])


async def test_get_assignment_is_per_workflow_and_revoking_hides_again(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    a = await wf_api.create(member)
    b = await wf_api.create(member)
    await db.assign(a["id"], second_member.id, member.id)
    assert (await wf_api.get(second_member, a["id"])).status_code == 200
    assert (await wf_api.get(second_member, b["id"])).status_code == 404
    r = await client.delete(
        f"/workflows/{a['id']}/assignments/{second_member.id}", headers=member.headers
    )
    assert r.status_code == 204
    assert (await wf_api.get(second_member, a["id"])).status_code == 404


async def test_get_response_is_redacted_view_with_iso_timestamps(
    member: Member, wf_api: WorkflowApi
) -> None:
    created = await wf_api.create(member)
    got = jbody(await wf_api.get(member, created["id"]))
    assert set(got) == {
        "id", "userId", "name", "description", "category", "tags", "difficulty",
        "estimatedTime", "nodes", "edges", "version", "isTemplate", "isPublic",
        "isProduction", "externalSlug", "createdAt", "updatedAt",
    }  # fmt: skip
    assert iso_dt(got["createdAt"]) <= iso_dt(got["updatedAt"])
