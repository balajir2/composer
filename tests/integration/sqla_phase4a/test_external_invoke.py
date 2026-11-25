"""Group 11: POST /api/run/{slug} (external invoke), async and sync modes."""

import asyncio
import time
from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK,
    Db,
    EnqueueRecorder,
    Member,
    approval_graph,
    install_email_recorder,
    install_enqueue,
    jbody,
    mint_api_key,
    unique,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _published(
    db: Db,
    owner: Member,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    wid = await db.seed_workflow(owner, nodes=nodes, edges=edges)
    slug = unique("p4a-ext")
    await db.publish_directly(wid, slug)
    return wid, slug


async def test_async_mode_returns_queued_with_exact_shape(
    client: AsyncClient, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid, slug = await _published(db, member)
    headers = await mint_api_key(client, member)

    r = await client.post(f"/api/run/{slug}", json={"input": {"q": 1}}, headers=headers)
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == {"executionId", "workflowId", "status", "streamUrl"}
    assert body["workflowId"] == wid and body["status"] == "queued"
    assert body["streamUrl"] == f"ws://test/executions/{body['executionId']}/ws"
    (row,) = await db.executions_of(wid)
    assert row.id == body["executionId"] and row.user_id == member.id
    assert row.status == "queued" and row.input == {"q": 1}
    assert rec.calls == [(row.id, "run")]


async def test_unknown_slug_and_missing_key(client: AsyncClient, member: Member) -> None:
    headers = await mint_api_key(client, member)
    r = await client.post("/api/run/no-such-slug", json={"input": 1}, headers=headers)
    assert r.status_code == 404
    assert jbody(r) == {"detail": "production workflow 'no-such-slug' not found"}
    r = await client.post("/api/run/no-such-slug", json={"input": 1})
    assert r.status_code in (401, 403)


async def test_sync_mode_returns_completed_output(
    client: AsyncClient, member: Member, db: Db
) -> None:
    wid, slug = await _published(db, member)  # default autouse driver runs claim-and-run inline
    headers = await mint_api_key(client, member)
    r = await client.post(
        f"/api/run/{slug}",
        json={"input": "ping", "sync": True, "timeoutSeconds": 30},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == {"executionId", "workflowId", "status", "output"}
    assert body["status"] == "completed" and body["output"] == "ping" and body["workflowId"] == wid
    row = await db.must(body["executionId"])
    assert row.status == "completed" and row.output == "ping" and row.user_id == member.id


async def test_sync_mode_returns_early_at_waiting_approval(
    client: AsyncClient, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_email_recorder(monkeypatch)
    nodes, edges = approval_graph()
    _wid, slug = await _published(db, member, nodes=nodes, edges=edges)
    headers = await mint_api_key(client, member)
    started = time.monotonic()
    r = await client.post(
        f"/api/run/{slug}", json={"input": "x", "sync": True, "timeoutSeconds": 30}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert time.monotonic() - started < 25  # did not poll to the timeout
    body = jbody(r)
    assert set(body) == {"executionId", "workflowId", "status", "output"}
    assert body["status"] == "waiting_approval" and body["output"] is None
    assert (await db.must(body["executionId"])).status == "waiting_approval"


async def test_sync_timeout_falls_back_to_async_shape_with_last_status(
    client: AsyncClient, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())  # nothing ever claims the row
    wid, slug = await _published(db, member)
    headers = await mint_api_key(client, member)
    started = time.monotonic()
    r = await client.post(
        f"/api/run/{slug}", json={"input": "x", "sync": True, "timeoutSeconds": 1}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert 0.9 < time.monotonic() - started < 10
    body = jbody(r)
    assert set(body) == {"executionId", "workflowId", "status", "streamUrl"}
    assert body["status"] == "queued" and body["workflowId"] == wid
    assert body["streamUrl"].endswith(f"/executions/{body['executionId']}/ws")
    assert rec.calls == [(body["executionId"], "run")]


async def test_enqueue_failure_is_not_marked_failed_on_the_external_route(
    client: AsyncClient, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D9 (kept): unlike POST /executions, the external route lets an enqueue error escape and
    leaves the row `queued` (the error is not turned into a failed row)."""
    install_enqueue(monkeypatch, EnqueueRecorder(raises=RuntimeError("enqueue down")))
    wid, slug = await _published(db, member)
    headers = await mint_api_key(client, member)
    with pytest.raises(RuntimeError, match="enqueue down"):
        await client.post(f"/api/run/{slug}", json={"input": "x"}, headers=headers)
    (row,) = await db.executions_of(wid)
    assert row.status == "queued" and row.error is None and row.completed_at is None


async def _transitions(
    db: Db, eid: str, steps: list[tuple[float, dict[str, Any]]]
) -> asyncio.Task[None]:
    async def _run() -> None:
        started = time.monotonic()
        for at, values in steps:
            await asyncio.sleep(max(0.0, at - (time.monotonic() - started)))
            await db.set_execution(eid, **values)

    return asyncio.create_task(_run())


async def test_sync_poll_observes_later_transitions(
    client: AsyncClient, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enqueue is a no-op that schedules background transitions, so the first poll sees a
    non-terminal row and the loop must keep polling until it turns terminal."""
    tasks: list[asyncio.Task[None]] = []

    async def _schedule(eid: str) -> None:
        tasks.append(
            await _transitions(
                db,
                eid,
                [
                    (0.4, {"status": "running"}),
                    (0.9, {"status": "completed", "output": "late"}),
                ],
            )
        )

    install_enqueue(monkeypatch, EnqueueRecorder(before=_schedule))
    wid, slug = await _published(db, member)
    headers = await mint_api_key(client, member)
    r = await client.post(
        f"/api/run/{slug}", json={"input": "x", "sync": True, "timeoutSeconds": 30}, headers=headers
    )
    await asyncio.gather(*tasks)
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == {"executionId", "workflowId", "status", "output"}
    assert body["status"] == "completed" and body["output"] == "late" and body["workflowId"] == wid


async def test_sync_timeout_reports_last_observed_status_running(
    client: AsyncClient, member: Member, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks: list[asyncio.Task[None]] = []

    async def _schedule(eid: str) -> None:
        tasks.append(await _transitions(db, eid, [(0.3, {"status": "running"})]))

    install_enqueue(monkeypatch, EnqueueRecorder(before=_schedule))
    _wid, slug = await _published(db, member)
    headers = await mint_api_key(client, member)
    r = await client.post(
        f"/api/run/{slug}", json={"input": "x", "sync": True, "timeoutSeconds": 1}, headers=headers
    )
    await asyncio.gather(*tasks)
    assert r.status_code == 200, r.text
    body = jbody(r)
    assert set(body) == {"executionId", "workflowId", "status", "streamUrl"}
    assert body["status"] == "running"
