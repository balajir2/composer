"""Who may start a run, and whose execution an idempotency key replays (real database)."""

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

_KEY_TAKEN = "idempotency key already used for this workflow"


async def test_idempotency_key_replays_only_for_the_original_caller(
    client: AsyncClient, member: Member, second_member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    wf = await wf_api.create(member, isPublic=True)
    wid = wf["id"]
    key = unique("idem")
    secret = "PRIVATE-INPUT-OF-FIRST-CALLER"

    first = await client.post(
        "/executions",
        json={"workflowId": wid, "input": secret, "idempotencyKey": key},
        headers=member.headers,
    )
    assert first.status_code == 202, first.text
    first_id = jbody(first)["id"]

    # Same caller retrying gets the same execution back, nothing new started.
    retry = await client.post(
        "/executions",
        json={"workflowId": wid, "input": secret, "idempotencyKey": key},
        headers=member.headers,
    )
    assert retry.status_code == 202, retry.text
    assert jbody(retry)["id"] == first_id

    # Another user reusing the key gets a 409 that reveals nothing about the first run.
    other = await client.post(
        "/executions",
        json={"workflowId": wid, "input": "mine", "idempotencyKey": key},
        headers=second_member.headers,
    )
    assert other.status_code == 409, other.text
    assert jbody(other)["detail"] == _KEY_TAKEN
    assert first_id not in other.text
    assert secret not in other.text
    assert len(await db.executions_for(wid)) == 1


async def test_external_invoke_idempotency_key_is_not_replayed_to_another_user(
    client: AsyncClient, member: Member, second_member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    wf = await wf_api.create(member, isPublic=True)
    slug = unique("idem").lower()
    assert (await wf_api.publish(member, wf, slug)).status_code == 200
    key = unique("idem")

    owner_key = await mint_api_key(client, member)
    other_key = await mint_api_key(client, second_member)
    first = await client.post(
        f"/api/run/{slug}", json={"input": "secret-in", "idempotencyKey": key}, headers=owner_key
    )
    assert first.status_code == 200, first.text
    first_id = jbody(first)["executionId"]

    retry = await client.post(
        f"/api/run/{slug}", json={"input": "secret-in", "idempotencyKey": key}, headers=owner_key
    )
    assert retry.status_code == 200, retry.text
    assert jbody(retry)["executionId"] == first_id

    other = await client.post(
        f"/api/run/{slug}", json={"input": "x", "idempotencyKey": key}, headers=other_key
    )
    assert other.status_code == 409, other.text
    assert jbody(other)["detail"] == _KEY_TAKEN
    assert first_id not in other.text
    assert len(await db.executions_for(wf["id"])) == 1
