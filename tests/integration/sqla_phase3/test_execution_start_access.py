"""Who may start a run, and whose execution an idempotency key replays (real database)."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    WorkflowApi,
    jbody,
    mint_api_key,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def test_same_idempotency_key_is_scoped_per_user_on_executions(
    client: AsyncClient, member: Member, second_member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    wf = await wf_api.create(member, isPublic=True)
    wid = wf["id"]
    key = unique("idem")

    async def post(who: Member, text: str) -> dict[str, Any]:
        resp = await client.post(
            "/executions",
            json={"workflowId": wid, "input": text, "idempotencyKey": key},
            headers=who.headers,
        )
        assert resp.status_code == 202, resp.text
        return jbody(resp)

    first = await post(member, "first-caller")
    second = await post(second_member, "second-caller")
    # Same key, different users: two distinct executions, no 409 and no cross-replay.
    assert first["id"] != second["id"]
    assert len(await db.executions_for(wid)) == 2

    # Each caller's retry replays their own execution and starts nothing new.
    assert (await post(member, "first-caller"))["id"] == first["id"]
    assert (await post(second_member, "second-caller"))["id"] == second["id"]
    assert len(await db.executions_for(wid)) == 2


async def test_same_idempotency_key_is_scoped_per_user_on_external_invoke(
    client: AsyncClient, member: Member, second_member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    wf = await wf_api.create(member, isPublic=True)
    slug = unique("idem").lower()
    assert (await wf_api.publish(member, wf, slug)).status_code == 200
    key = unique("idem")
    headers = {
        "owner": await mint_api_key(client, member),
        "other": await mint_api_key(client, second_member),
    }

    async def run(who: str) -> str:
        resp = await client.post(
            f"/api/run/{slug}", json={"input": who, "idempotencyKey": key}, headers=headers[who]
        )
        assert resp.status_code == 200, resp.text
        return str(jbody(resp)["executionId"])

    first = await run("owner")
    second = await run("other")
    assert first != second
    assert len(await db.executions_for(wf["id"])) == 2

    assert await run("owner") == first
    assert await run("other") == second
    assert len(await db.executions_for(wf["id"])) == 2


async def test_an_assignee_can_start_a_run_and_a_bystander_cannot(
    client: AsyncClient,
    member: Member,
    second_member: Member,
    third_member: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    wf = await wf_api.create(member, isPublic=False)
    wid = wf["id"]
    await db.assign(wid, second_member.id, member.id)

    ok = await client.post(
        "/executions", json={"workflowId": wid, "input": "a"}, headers=second_member.headers
    )
    assert ok.status_code == 202, ok.text
    assert jbody(ok)["userId"] == second_member.id

    denied = await client.post(
        "/executions", json={"workflowId": wid, "input": "b"}, headers=third_member.headers
    )
    assert denied.status_code == 404, denied.text
    assert jbody(denied)["detail"] == f"Workflow {wid!r} not found."
    assert [e.user_id for e in await db.executions_for(wid)] == [second_member.id]
