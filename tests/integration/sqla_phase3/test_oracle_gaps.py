"""Gap-closers found in review: builder inputs and load-time semantics, the file-trigger poller,
executor use of the stored graph and secrets, column preservation, deep JSON, execution owners."""

import re
from typing import Any, cast

import httpx
import pytest
from httpx import AsyncClient
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.security.encryption import decrypt_marked, encrypt_marked
from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    LONG_AGO,
    REDACTED,
    Db,
    Member,
    Phase3Adapter,
    WorkflowApi,
    chain,
    decrypts_to,
    http_node,
    jbody,
    jira,
    mint_api_key,
    node,
    node_of,
    payload,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


# --------------------------------------------------------------------------- builder


async def test_builder_hands_the_llm_the_stored_workflow(
    member: Member, wf_api: WorkflowApi, adapter: Phase3Adapter, db: Db
) -> None:
    nodes, edges = chain(jira("j", "llm-visible-secret"), node("t", "transform"))
    created = await wf_api.create(
        member, nodes=nodes, edges=edges, description="seeded description"
    )
    wid = created["id"]
    stored_cipher = (await db.stored_node(wid, "j"))["data"]["apiToken"]

    n2, e2 = chain(node("t", "transform"))
    r = await adapter.builder_turn(member, wid, n2, e2)
    assert r.status_code == 200, r.text
    (call,) = adapter.builder_calls
    seen = call["workflow"]
    assert seen.id == wid and seen.name == created["name"]
    assert seen.description == "seeded description"
    assert [n.id for n in seen.nodes] == [n["id"] for n in created["nodes"]]
    assert [e.id for e in seen.edges] == [e["id"] for e in created["edges"]]
    # PINNED: the model is handed the STORED ciphertext for a secret, neither plaintext nor marker
    seen_jira = next(n for n in seen.nodes if n.id == "j")
    assert seen_jira.data.api_token == stored_cipher
    assert decrypts_to(seen_jira.data.api_token, "llm-visible-secret")
    assert call["workflow_id"] == wid and call["execution_id"] is None


async def test_builder_save_echo_resolves_against_the_row_loaded_before_the_turn(
    member: Member, wf_api: WorkflowApi, adapter: Phase3Adapter, db: Db
) -> None:
    """PINNED: the stored ciphertext a redacted echo resolves to is the one read when the route
    LOADED the workflow, not whatever is stored when the turn finishes. If the row changes
    during the (slow) LLM turn, the echo restores the load-time value."""
    nodes, edges = chain(jira("j", "original-secret"))
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]
    load_time = (await db.stored_node(wid, "j"))["data"]["apiToken"]
    swapped = encrypt_marked("swapped-during-turn")
    assert swapped != load_time

    async def rewrite_during_turn() -> None:
        row = await db.must(wid)
        new_nodes = [dict(n) for n in cast("list[dict[str, Any]]", row.nodes)]
        for n in new_nodes:
            if n["id"] == "j":
                n["data"] = {**n["data"], "apiToken": swapped}
        await db.set_workflow(wid, nodes=new_nodes)
        assert (await db.stored_node(wid, "j"))["data"]["apiToken"] == swapped

    n2, e2 = chain(jira("j", REDACTED))
    r = await adapter.builder_turn(member, wid, n2, e2, during_turn=rewrite_during_turn)
    assert r.status_code == 200, r.text
    kept = (await db.stored_node(wid, "j"))["data"]["apiToken"]
    assert kept == load_time
    assert decrypt_marked(kept) == "original-secret"


# --------------------------------------------------------------------------- poller

DRIVE_URL = re.compile(r"https://www\.googleapis\.com/(upload/)?drive/v3/files.*")


def trigger(connection_id: str, folder: str) -> dict[str, Any]:
    return node(
        "ft",
        "file-trigger",
        provider="google-drive",
        connectionId=connection_id,
        driveFolderId=folder,
        targetInputVariable="doc",
    )


async def test_poller_only_reads_production_workflows(
    member: Member,
    adapter: Phase3Adapter,
    db: Db,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    seen: list[httpx.Request] = []

    def drive(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"files": []})

    httpx_mock.add_callback(drive, url=DRIVE_URL, is_reusable=True)  # pyright: ignore[reportUnknownMemberType]
    connection = await db.seed_connection(member.id)
    draft_folder, live_folder = unique("folder-draft"), unique("folder-live")
    nodes_draft, edges_draft = chain(trigger(connection, draft_folder))
    nodes_live, edges_live = chain(trigger(connection, live_folder))
    await db.seed(member, nodes=nodes_draft, edges=edges_draft, is_production=False)
    await db.seed(
        member,
        nodes=nodes_live,
        edges=edges_live,
        is_production=True,
        external_slug=unique("poll").lower(),
    )

    await adapter.poll_file_triggers()

    queries = [r.url.params.get("q", "") for r in seen]
    assert any(live_folder in q for q in queries), "control: the production workflow is polled"
    assert not any(draft_folder in q for q in queries), "a non-production workflow is ignored"


# --------------------------------------------------------------------------- executor


async def test_executor_runs_the_stored_graph(
    member: Member, adapter: Phase3Adapter, db: Db
) -> None:
    marker = unique("distinct-output")
    nodes, edges = chain(node("set", "set-state", stateKey="lastOutput", stateValue=marker))
    wid = await db.seed(member, nodes=nodes, edges=edges)
    result = await adapter.run_execution(wid, member.id, "ignored-input")
    assert result["status"] == "completed", result
    assert result["output"] == marker


async def test_executor_decrypts_stored_http_secret_for_the_wire(
    member: Member,
    wf_api: WorkflowApi,
    adapter: Phase3Adapter,
    db: Db,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    sent: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    httpx_mock.add_callback(api, url="https://8.8.8.8/x", is_reusable=True)  # pyright: ignore[reportUnknownMemberType]
    wire = http_node("h", "Bearer wire-secret-123")
    wire["data"]["httpUrl"] = (
        "https://8.8.8.8/x"  # a literal public IP: the SSRF guard resolves names
    )
    nodes, edges = chain(wire)
    created = await wf_api.create(member, nodes=nodes, edges=edges)  # encrypted by the API
    stored = (await db.stored_node(created["id"], "h"))["data"]["httpHeaders"]["Authorization"]
    assert stored.startswith("enc:v1:") and "wire-secret-123" not in stored

    result = await adapter.run_execution(created["id"], member.id)
    assert result["status"] == "completed", result
    assert [r.headers["authorization"] for r in sent] == ["Bearer wire-secret-123"]
    assert sent[0].headers["content-type"] == "application/json"
    assert "wire-secret-123" not in str(result)


# --------------------------------------------------------------------------- columns + JSON


async def test_put_changes_only_the_payload_columns(
    member: Member, second_member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    slug = unique("keep").lower()
    wid = await db.seed(
        member,
        description="d",
        original_owner_email="former.owner@example.com",
        is_production=True,
        external_slug=slug,
        created_at=LONG_AGO,
        updated_at=LONG_AGO,
    )
    await db.assign(wid, second_member.id, member.id)
    before = db.snapshot(await db.must(wid))
    nodes, edges = chain(node("t", "transform"))
    allowed = {
        "name", "description", "category", "tags", "difficulty", "estimated_time",
        "nodes", "edges", "version", "is_template", "is_public", "updated_at",
    }  # fmt: skip
    for who in (member, second_member):
        r = await wf_api.put(
            who,
            wid,
            payload(nodes, edges, name=f"by {who.id}", category="c", tags=["a"], isPublic=True),
        )
        assert r.status_code == 200, r.text
        after = db.snapshot(await db.must(wid))
        changed = {k for k in before if before[k] != after[k]}
        assert changed <= allowed, changed - allowed
        assert {"name", "nodes", "updated_at"} <= changed
        for kept in ("id", "user_id", "created_at", "original_owner_email", "is_production",
                     "external_slug"):  # fmt: skip
            assert after[kept] == before[kept], kept
        assert after["original_owner_email"] == "former.owner@example.com"
        assert after["is_production"] is True and after["external_slug"] == slug


async def test_deep_json_round_trips_exactly(
    client: AsyncClient, member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    deep: dict[str, Any] = {
        "unicode": "héllo wörld — 日本語 😀",
        "float": 1.5,
        "tiny": 0.000125,
        "big": 12345678901,
        "flag": True,
        "off": False,
        "nothing": None,
        "empty_list": [],
        "empty_obj": {},
        "nested": [[1, [2.25, "é"]], {"k": [None, False, {"deep": ["x", 3.5]}]}],
    }
    mid = node("t", "transform", transformScript="print('héllo')", **deep)
    nodes, edges = chain(mid)
    created = await wf_api.create(member, nodes=nodes, edges=edges)
    wid = created["id"]

    row = await db.must(wid)
    stored = node_of(row.nodes, "t")["data"]
    from_get = node_of(jbody(await wf_api.get(member, wid))["nodes"], "t")["data"]
    for key, value in deep.items():
        assert stored[key] == value and type(stored[key]) is type(value), key
        assert from_get[key] == value and type(from_get[key]) is type(value), key
    assert stored == from_get
    assert node_of(row.nodes, "t")["position"] == {"x": 200.0, "y": 0.0}
    assert row.nodes == jbody(await wf_api.get(member, wid))["nodes"]
    assert row.edges == jbody(await wf_api.get(member, wid))["edges"]


# --------------------------------------------------------------------------- execution owner


async def test_executions_record_the_caller_as_user(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    created = await wf_api.create(member, isPublic=True)
    slug = unique("pub").lower()
    r = await wf_api.publish(member, created, slug)
    assert r.status_code == 200
    wid = created["id"]

    key = await mint_api_key(client, second_member)
    r = await client.post(f"/api/run/{slug}", json={"input": "a"}, headers=key)
    assert r.status_code == 200, r.text
    r = await client.post(
        "/executions", json={"workflowId": wid, "input": "b"}, headers=third_member.headers
    )
    assert r.status_code == 202, r.text
    r = await client.post(
        "/executions", json={"workflowId": wid, "input": "c"}, headers=member.headers
    )
    assert r.status_code == 202, r.text

    owners = sorted(e.user_id or "" for e in await db.executions_for(wid))
    assert owners == sorted([second_member.id, third_member.id, member.id])
