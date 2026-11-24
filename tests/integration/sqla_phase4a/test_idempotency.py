"""Group 2: idempotency keys on POST /executions and POST /api/run/{slug}.

Keys are unique per (workflow, user) (Alembic 0002). The 409 "key already used" path needs the
old index and is unit-tested only.
"""

import asyncio

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    ExecApi,
    Member,
    install_enqueue,
    jbody,
    mint_api_key,
    publish,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def test_same_user_same_key_returns_same_execution(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    key = unique("idem")
    first = await exec_api.create(member, wid, {"n": 1}, idempotency_key=key)
    second = await exec_api.create(member, wid, {"n": 2}, idempotency_key=key)
    assert first.status_code == 202 and second.status_code == 202
    assert jbody(second) == jbody(first)
    assert jbody(second)["input"] == {"n": 1}  # the replay does not rewrite the first request
    rows = await db.executions_of(wid)
    assert [r.id for r in rows] == [jbody(first)["id"]]
    assert rows[0].idempotency_key == key
    assert rec.calls == [(jbody(first)["id"], "run")]


async def test_no_key_means_independent_executions(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    a = await exec_api.create(member, wid)
    b = await exec_api.create(member, wid)
    assert jbody(a)["id"] != jbody(b)["id"]
    assert len(await db.executions_of(wid)) == 2 and len(rec.calls) == 2


async def test_different_key_or_workflow_is_a_new_execution(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    other = await db.seed_workflow(member)
    key = unique("idem")
    ids = {
        jbody(await exec_api.create(member, wid, idempotency_key=key))["id"],
        jbody(await exec_api.create(member, wid, idempotency_key=unique("idem")))["id"],
        jbody(await exec_api.create(member, other, idempotency_key=key))["id"],
    }
    assert len(ids) == 3 and len(rec.calls) == 3


async def test_other_user_same_key_gets_own_execution(
    member: Member,
    second_member: Member,
    exec_api: ExecApi,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member, is_public=True)
    key = unique("idem")
    a = await exec_api.create(member, wid, idempotency_key=key)
    b = await exec_api.create(second_member, wid, idempotency_key=key)
    assert a.status_code == 202 and b.status_code == 202
    assert jbody(a)["id"] != jbody(b)["id"]
    assert jbody(a)["userId"] == member.id and jbody(b)["userId"] == second_member.id
    rows = await db.executions_of(wid)
    assert sorted(r.user_id or "" for r in rows) == sorted([member.id, second_member.id])
    assert {r.idempotency_key for r in rows} == {key}
    assert len(rec.calls) == 2
    # each caller's own replay still resolves to its own row
    a2 = await exec_api.create(member, wid, idempotency_key=key)
    b2 = await exec_api.create(second_member, wid, idempotency_key=key)
    assert jbody(a2)["id"] == jbody(a)["id"] and jbody(b2)["id"] == jbody(b)["id"]
    assert len(rec.calls) == 2


async def test_replay_after_completion_returns_completed_row(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    key = unique("idem")
    first = await exec_api.create(member, wid, idempotency_key=key)
    eid = jbody(first)["id"]
    await db.set_execution(eid, status="completed", output={"answer": 42})
    replay = await exec_api.create(member, wid, idempotency_key=key)
    assert replay.status_code == 202
    body = jbody(replay)
    assert body["id"] == eid and body["status"] == "completed" and body["output"] == {"answer": 42}
    assert len(rec.calls) == 1 and len(await db.executions_of(wid)) == 1


async def test_five_concurrent_same_key_posts_create_one_row(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    key = unique("idem")
    responses = await asyncio.gather(
        *(exec_api.create(member, wid, idempotency_key=key) for _ in range(6))
    )
    assert [r.status_code for r in responses] == [202] * 6, [r.text for r in responses]
    ids = {jbody(r)["id"] for r in responses}
    assert len(ids) == 1
    rows = await db.executions_of(wid)
    assert [r.id for r in rows] == list(ids)
    assert rec.calls == [(next(iter(ids)), "run")]


# --------------------------------------------------------------------------- POST /api/run/{slug}


async def test_external_invoke_replay_skips_enqueue(
    client: AsyncClient,
    member: Member,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    slug = unique("p4a-slug")
    await publish(client, member, wid, slug)
    headers = await mint_api_key(client, member)
    key = unique("idem")

    first = await client.post(
        f"/api/run/{slug}", json={"input": {"v": 1}, "idempotencyKey": key}, headers=headers
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/run/{slug}", json={"input": {"v": 2}, "idempotencyKey": key}, headers=headers
    )
    assert second.status_code == 200, second.text
    assert jbody(second) == jbody(first)
    assert set(jbody(first)) == {"executionId", "workflowId", "status", "streamUrl"}
    assert jbody(first)["status"] == "queued" and jbody(first)["workflowId"] == wid
    rows = await db.executions_of(wid)
    assert [r.id for r in rows] == [jbody(first)["executionId"]]
    assert rows[0].idempotency_key == key and rows[0].input == {"v": 1}
    assert rec.calls == [(jbody(first)["executionId"], "run")]


async def test_external_invoke_five_concurrent_same_key(
    client: AsyncClient,
    member: Member,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    slug = unique("p4a-slug")
    await publish(client, member, wid, slug)
    headers = await mint_api_key(client, member)
    key = unique("idem")
    responses = await asyncio.gather(
        *(
            client.post(
                f"/api/run/{slug}", json={"input": "x", "idempotencyKey": key}, headers=headers
            )
            for _ in range(6)
        )
    )
    assert [r.status_code for r in responses] == [200] * 6, [r.text for r in responses]
    ids = {jbody(r)["executionId"] for r in responses}
    assert len(ids) == 1
    assert [r.id for r in await db.executions_of(wid)] == list(ids)
    assert rec.calls == [(next(iter(ids)), "run")]
