"""Groups 11-13: builder save, external invoke + execution start, executor workflow read."""

import asyncio
import logging
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    LONG_AGO,
    REDACTED,
    Db,
    Member,
    Phase3Adapter,
    WorkflowApi,
    chain,
    confluence,
    decrypts_to,
    http_node,
    jbody,
    jira,
    mint_api_key,
    node,
    node_of,
    not_found,
    unique,
    vector_db,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


# --------------------------------------------------------------------------- 11. builder save


def secret_nodes(prefix: str = "") -> list[dict[str, Any]]:
    return [
        jira(f"{prefix}j", "b-jira-secret"),
        vector_db(f"{prefix}v", "b-vdb-secret", "b-emb-secret"),
        confluence(f"{prefix}c", "b-conf-secret"),
        http_node(f"{prefix}h", "Bearer b-http-secret"),
    ]


async def test_builder_turn_persists_only_nodes_and_edges(
    client: AsyncClient,
    member: Member,
    adapter: Phase3Adapter,
    db: Db,
) -> None:
    wid = await db.seed(
        member,
        name="original name",
        description="original description",
        category="cat",
        tags=["t"],
        is_public=True,
        is_template=True,
        version="9",
    )
    bystander = await db.seed(member)
    before_bystander = db.snapshot(await db.must(bystander))
    await db.backdate(wid)
    before = db.snapshot(await db.must(wid))

    nodes, edges = chain(node("t", "transform", transformScript="x = 1"))
    r = await adapter.builder_turn(member, wid, nodes, edges, rename_to="renamed by builder")
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert body["reply"] == "scripted reply" and body["events"] == [{"type": "scripted"}]
    assert body["sessionId"] and isinstance(body["validation_errors"], list)
    assert body["workflow"]["id"] == wid
    assert [n["id"] for n in body["workflow"]["nodes"]] == ["s", "t", "e"]

    after = db.snapshot(await db.must(wid))
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"nodes", "edges", "updated_at"}, changed  # name/description untouched
    assert after["updated_at"] > LONG_AGO
    assert body["workflow"]["name"] == "original name"
    assert [n["id"] for n in after["nodes"]] == ["s", "t", "e"]
    assert db.snapshot(await db.must(bystander)) == before_bystander
    assert len(adapter.builder_calls) == 1
    call = adapter.builder_calls[0]
    assert call["workflow_id"] == wid and call["message"] == "build it"


async def test_builder_turn_encrypts_secrets_and_preserves_on_echo(
    client: AsyncClient,
    member: Member,
    adapter: Phase3Adapter,
    db: Db,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    wid = await db.seed(member)
    nodes, edges = chain(*secret_nodes())
    r = await adapter.builder_turn(member, wid, nodes, edges)
    assert r.status_code == 200, r.text
    plain = ["b-jira-secret", "b-vdb-secret", "b-emb-secret", "b-conf-secret", "http-secret"]

    row = await db.must(wid)
    jd = node_of(row.nodes, "j")["data"]
    vd = node_of(row.nodes, "v")["data"]
    cd = node_of(row.nodes, "c")["data"]
    hd = node_of(row.nodes, "h")["data"]["httpHeaders"]
    assert decrypts_to(jd["apiToken"], "b-jira-secret")
    assert decrypts_to(vd["vectorDbApiKey"], "b-vdb-secret")
    assert decrypts_to(vd["vectorDbEmbeddingApiKey"], "b-emb-secret")
    assert decrypts_to(cd["apiToken"], "b-conf-secret")
    assert decrypts_to(hd["Authorization"], "Bearer b-http-secret")
    assert hd["Content-Type"] == "application/json"
    cipher = [
        jd["apiToken"], vd["vectorDbApiKey"], vd["vectorDbEmbeddingApiKey"], cd["apiToken"],
        hd["Authorization"],
    ]  # fmt: skip

    shown = jbody(r)["workflow"]["nodes"]
    assert node_of(shown, "j")["data"]["apiToken"] == REDACTED
    assert node_of(shown, "v")["data"]["vectorDbApiKey"] == REDACTED
    assert node_of(shown, "v")["data"]["vectorDbEmbeddingApiKey"] == REDACTED
    assert node_of(shown, "c")["data"]["apiToken"] == REDACTED
    assert node_of(shown, "h")["data"]["httpHeaders"]["Authorization"] == REDACTED
    for text_ in (r.text, "\n".join(rec.getMessage() for rec in caplog.records)):
        for s in [*plain, *cipher]:
            assert s not in text_, s

    # second turn: the scripted builder hands back the redacted view -> stored values preserved
    echo = [
        jira("j", REDACTED),
        vector_db("v", REDACTED, REDACTED),
        confluence("c", REDACTED),
        http_node("h", REDACTED),
    ]
    n2, e2 = chain(*echo)
    await db.backdate(wid)
    r = await adapter.builder_turn(member, wid, n2, e2)
    assert r.status_code == 200, r.text
    row = await db.must(wid)
    again = [
        node_of(row.nodes, "j")["data"]["apiToken"],
        node_of(row.nodes, "v")["data"]["vectorDbApiKey"],
        node_of(row.nodes, "v")["data"]["vectorDbEmbeddingApiKey"],
        node_of(row.nodes, "c")["data"]["apiToken"],
        node_of(row.nodes, "h")["data"]["httpHeaders"]["Authorization"],
    ]
    assert again == cipher
    assert row.updated_at > LONG_AGO

    # a marker for a node id the stored row does not have stores None (HTTP: the marker itself)
    n3, e3 = chain(
        jira("new-j", REDACTED),
        vector_db("new-v", REDACTED, REDACTED),
        confluence("new-c", REDACTED),
        http_node("new-h", REDACTED),
    )
    r = await adapter.builder_turn(member, wid, n3, e3)
    assert r.status_code == 200, r.text
    row = await db.must(wid)
    assert node_of(row.nodes, "new-j")["data"]["apiToken"] is None
    assert node_of(row.nodes, "new-v")["data"]["vectorDbApiKey"] is None
    assert node_of(row.nodes, "new-v")["data"]["vectorDbEmbeddingApiKey"] is None
    assert node_of(row.nodes, "new-c")["data"]["apiToken"] is None
    assert node_of(row.nodes, "new-h")["data"]["httpHeaders"]["Authorization"] == REDACTED


async def test_builder_visibility_owner_assignee_admin_allowed_others_404(
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    adapter: Phase3Adapter,
    db: Db,
) -> None:
    wid = await db.seed(member, is_public=True)  # public does NOT grant builder access
    await db.assign(wid, second_member.id, member.id)
    nodes, edges = chain(node("t", "transform"))
    for who in (member, second_member, admin):
        r = await adapter.builder_turn(who, wid, nodes, edges)
        assert r.status_code == 200, (who.id, r.text)

    calls_before = len(adapter.builder_calls)
    await db.backdate(wid)
    frozen = db.snapshot(await db.must(wid))
    r = await adapter.builder_turn(third_member, wid, nodes, edges)
    assert r.status_code == 404 and r.json()["detail"] == "Workflow not found."
    assert len(adapter.builder_calls) == calls_before  # the LLM turn never ran
    assert db.snapshot(await db.must(wid)) == frozen

    r = await adapter.builder_turn(member, "no-such-workflow", nodes, edges)
    assert r.status_code == 404 and r.json()["detail"] == not_found("no-such-workflow")


async def test_builder_size_limit_rejects_without_saving(
    member: Member, adapter: Phase3Adapter, db: Db
) -> None:
    wid = await db.seed(member)
    await db.backdate(wid)
    frozen = db.snapshot(await db.must(wid))
    nodes, edges = chain(*[node(f"n{i}", "transform") for i in range(99)])
    r = await adapter.builder_turn(member, wid, nodes, edges)
    assert r.status_code == 422
    assert r.json()["detail"] == "workflow exceeds max_nodes=100; got 101"
    assert db.snapshot(await db.must(wid)) == frozen


async def test_builder_unknown_session_expires_before_the_workflow_is_loaded(
    client: AsyncClient, member: Member
) -> None:
    r = await client.post(
        "/builder/no-such-workflow/message",
        json={"message": "hi", "sessionId": "gone"},
        headers=member.headers,
    )
    assert r.status_code == 200
    assert jbody(r) == {
        "sessionId": "gone",
        "events": [{"type": "session_expired"}],
        "reply": "This build session has expired. Please start over.",
        "workflow": None,
        "validation_errors": [],
    }


# --------------------------------------------------------------------------- 12. invoke + start


async def _wait_done(client: AsyncClient, who: Member, execution_id: str) -> dict[str, Any]:
    for _ in range(100):
        r = await client.get(f"/executions/{execution_id}", headers=who.headers)
        body = jbody(r)
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.1)
    raise AssertionError("execution did not finish")


async def _published(
    wf_api: WorkflowApi, owner: Member, *, public: bool = False
) -> tuple[dict[str, Any], str]:
    created = await wf_api.create(owner, isPublic=public)
    s = unique("run").lower()
    r = await wf_api.publish(owner, created, s)
    assert r.status_code == 200, r.text
    return jbody(r), s


async def test_run_external_authorisation_matrix(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    private, private_slug = await _published(wf_api, member)
    public, public_slug = await _published(wf_api, member, public=True)
    await db.assign(private["id"], second_member.id, member.id)
    keys = {
        m.id: await mint_api_key(client, m) for m in (member, second_member, third_member, admin)
    }
    miss = f"production workflow {private_slug!r} not found"

    # private: owner and admin keys run it; an assignee's or a stranger's key does not
    for who in (member, admin):
        r = await client.post(
            f"/api/run/{private_slug}", json={"input": "hi"}, headers=keys[who.id]
        )
        assert r.status_code == 200, (who.id, r.text)
        body = jbody(r)
        assert body["workflowId"] == private["id"] and body["executionId"]
        assert body["status"] == "queued" and "/executions/" in body["streamUrl"]
        done = await _wait_done(client, who, body["executionId"])
        assert done["status"] == "completed" and done["output"] == "hi"
    for who in (second_member, third_member):
        r = await client.post(f"/api/run/{private_slug}", json={"input": "x"}, headers=keys[who.id])
        assert r.status_code == 404 and r.json()["detail"] == miss
    # executions belong to the key owner
    owners = {e.user_id for e in await db.executions_for(private["id"])}
    assert owners == {member.id, admin.id}

    # public: any valid key
    for who in (second_member, third_member):
        r = await client.post(f"/api/run/{public_slug}", json={"input": "p"}, headers=keys[who.id])
        assert r.status_code == 200, r.text
        assert jbody(r)["workflowId"] == public["id"]


async def test_run_external_requires_production_and_known_slug(
    client: AsyncClient, member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    key = await mint_api_key(client, member)
    # a slug on a row that is not (any longer) production
    wid = await db.seed(member, is_production=False, external_slug=unique("draft").lower())
    slug_ = (await db.must(wid)).external_slug
    r = await client.post(f"/api/run/{slug_}", json={"input": 1}, headers=key)
    assert r.status_code == 404 and r.json()["detail"] == f"production workflow {slug_!r} not found"
    r = await client.post("/api/run/no-such-slug", json={"input": 1}, headers=key)
    assert (
        r.status_code == 404
        and r.json()["detail"] == "production workflow 'no-such-slug' not found"
    )
    assert await db.executions_for(wid) == []

    # unpublishing makes a published slug unusable
    created, s = await _published(wf_api, member)
    assert (await client.post(f"/api/run/{s}", json={"input": 1}, headers=key)).status_code == 200
    r = await wf_api.put(member, created["id"], {**created, "isProduction": False})
    assert r.status_code == 200
    r = await client.post(f"/api/run/{s}", json={"input": 1}, headers=key)
    assert r.status_code == 404


async def test_run_external_needs_an_api_key_not_a_session_token(
    client: AsyncClient, member: Member, wf_api: WorkflowApi
) -> None:
    _, s = await _published(wf_api, member)
    r = await client.post(f"/api/run/{s}", json={"input": 1})
    assert r.status_code == 401
    r = await client.post(f"/api/run/{s}", json={"input": 1}, headers=member.headers)
    assert r.status_code == 401


async def test_run_external_sync_returns_the_output(
    client: AsyncClient, member: Member, wf_api: WorkflowApi
) -> None:
    created, s = await _published(wf_api, member)
    key = await mint_api_key(client, member)
    r = await client.post(
        f"/api/run/{s}", json={"input": "sync-in", "sync": True, "timeoutSeconds": 30}, headers=key
    )
    body = jbody(r)
    assert r.status_code == 200
    assert body["status"] == "completed" and body["output"] == "sync-in"
    assert body["workflowId"] == created["id"]


async def test_execution_start_visibility_owner_assignee_admin_public(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    db: Db,
) -> None:
    """POST /executions: owner, admin, an assignee, and anyone for a PUBLIC workflow may start a
    run; a bystander on a private workflow gets 404."""
    private = await db.seed(member)
    public = await db.seed(member, is_public=True)
    await db.assign(private, second_member.id, member.id)

    for who in (member, admin, second_member):
        r = await client.post(
            "/executions", json={"workflowId": private, "input": "go"}, headers=who.headers
        )
        assert r.status_code == 202, (who.id, r.text)
        body = jbody(r)
        assert body["workflowId"] == private and body["status"] == "queued"
        done = await _wait_done(client, who, body["id"])
        assert done["status"] == "completed" and done["output"] == "go"
    assert {e.user_id for e in await db.executions_for(private)} == {
        member.id,
        admin.id,
        second_member.id,
    }

    r = await client.post(
        "/executions", json={"workflowId": private, "input": "x"}, headers=third_member.headers
    )
    assert r.status_code == 404 and r.json()["detail"] == not_found(private)
    assert len(await db.executions_for(private)) == 3  # the rejected call created nothing

    for who in (second_member, third_member):
        r = await client.post(
            "/executions", json={"workflowId": public, "input": "x"}, headers=who.headers
        )
        assert r.status_code == 202, r.text

    r = await client.post(
        "/executions", json={"workflowId": "no-such", "input": "x"}, headers=member.headers
    )
    assert r.status_code == 404 and r.json()["detail"] == not_found("no-such")


# --------------------------------------------------------------------------- 13. executor read


async def test_executor_runs_an_existing_workflow(
    member: Member, adapter: Phase3Adapter, db: Db
) -> None:
    wid = await db.seed(member)
    result = await adapter.run_execution(wid, member.id, "payload")
    assert result["status"] == "completed" and result["output"] == "payload"
    assert result["error"] is None
    assert len(await db.executions_for(wid)) == 1


async def test_executor_reads_the_current_row_not_a_cached_copy(
    member: Member, adapter: Phase3Adapter, db: Db
) -> None:
    wid = await db.seed(member)
    assert (await adapter.run_execution(wid, member.id))["status"] == "completed"
    # break the stored graph (no nodes): the next run reads the new row and fails
    await db.set_workflow(wid, nodes=[], edges=[])
    broken = await adapter.run_execution(wid, member.id)
    assert broken["status"] == "failed" and broken["error"]


async def test_executor_missing_workflow_raises_runtime_error(adapter: Phase3Adapter) -> None:
    with pytest.raises(RuntimeError) as err:
        await adapter.prepare_compiled_for("no-such-workflow")
    assert str(err.value) == "Workflow 'no-such-workflow' not found"


async def test_executor_run_after_workflow_deleted_finds_no_execution(
    member: Member, adapter: Phase3Adapter, db: Db
) -> None:
    """Deleting a workflow cascades its executions at the DB level, so the executor's own
    'workflow missing' path is only reachable through the direct read above; running an
    execution whose workflow vanished is a no-op because the execution row is gone too."""
    wid = await db.seed(member)
    survived = await adapter.run_after_workflow_deleted(wid, member.id)
    assert survived is False
    assert await db.workflow(wid) is None
