"""Group 1: POST /executions."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    EXECUTION_KEYS,
    HTTPX_MARK,
    ISO,
    UUID_RE,
    Db,
    EnqueueRecorder,
    ExecApi,
    Member,
    ago,
    install_enqueue,
    jbody,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def test_create_202_exact_shape_and_row(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    other_wid = await db.seed_workflow(member)
    bystander = await db.seed_execution(other_wid, member, status="queued")
    before_bystander = db.snapshot(await db.must(bystander))

    r = await exec_api.create(member, wid, {"a": [1, 2], "b": "x"})
    assert r.status_code == 202, r.text
    body = jbody(r)
    assert set(body) == EXECUTION_KEYS
    assert body["workflowId"] == wid and body["userId"] == member.id
    assert body["status"] == "queued"
    assert ISO.match(body["startedAt"]) and UUID_RE.match(body["threadId"])
    assert body["completedAt"] is None and body["currentNodeId"] is None
    assert body["output"] is None and body["error"] is None and body["failureCode"] is None
    assert body["nodeResults"] == {} and body["variables"] == {}
    assert body["input"] == {"a": [1, 2], "b": "x"}

    row = await db.must(body["id"])
    assert row.status == "queued" and row.workflow_id == wid and row.user_id == member.id
    assert row.node_results == {} and row.variables == {}
    assert row.delivery_attempts == 0
    assert row.lease_owner is None and row.lease_expires_at is None and row.running_since is None
    assert row.idempotency_key is None and row.failure_code is None and row.error is None
    assert row.thread_id == body["threadId"] and row.input == {"a": [1, 2], "b": "x"}
    assert rec.calls == [(body["id"], "run")]
    assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_create_null_input_storage_today(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins how ``input=None`` is stored today (Task 2's `_json` helper must reproduce it)."""
    install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    r = await exec_api.create(member, wid, None)
    assert r.status_code == 202, r.text
    assert jbody(r)["input"] is None
    rows = await db.raw(
        "SELECT input IS NULL AS is_null, jsonb_typeof(input) AS jtype FROM workflow_executions "
        "WHERE id = :i",
        i=jbody(r)["id"],
    )
    assert tuple(rows[0]) == (False, "null"), tuple(rows[0])


async def test_create_missing_input_defaults_to_json_null(
    member: Member, client: AsyncClient, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_enqueue(monkeypatch, EnqueueRecorder())
    wid = await db.seed_workflow(member)
    r = await client.post("/executions", json={"workflowId": wid}, headers=member.headers)
    assert r.status_code == 202, r.text
    rows = await db.raw(
        "SELECT input IS NULL AS is_null, jsonb_typeof(input) AS jtype FROM workflow_executions "
        "WHERE id = :i",
        i=jbody(r)["id"],
    )
    assert tuple(rows[0]) == (False, "null")


async def test_create_row_committed_before_enqueue(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    async def _before(execution_id: str) -> None:
        row = await db.execution(execution_id)  # a separate session
        assert row is not None
        seen.append({"status": row.status, "user": row.user_id, "input": row.input})

    rec = install_enqueue(monkeypatch, EnqueueRecorder(before=_before))
    wid = await db.seed_workflow(member)
    r = await exec_api.create(member, wid, "hi")
    assert r.status_code == 202
    assert seen == [{"status": "queued", "user": member.id, "input": "hi"}]
    assert len(rec.calls) == 1


async def test_create_413_exact_detail_and_no_row(
    member: Member,
    exec_api: ExecApi,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import get_settings

    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    monkeypatch.setenv("MAX_EXECUTION_INPUT_BYTES", "50")
    get_settings.cache_clear()
    wid = await db.seed_workflow(member)
    r = await exec_api.create(member, wid, "x" * 100)
    assert r.status_code == 413, r.text
    assert jbody(r) == {"detail": "execution input exceeds max_bytes=50; got 102"}
    assert await db.executions_of(wid) == [] and rec.calls == []
    # non-ASCII is measured in UTF-8 bytes, not escaped characters: 3 chars * 3 bytes + 2 quotes
    ok = await exec_api.create(member, wid, "€" * 16)  # 48 + 2 = 50 bytes exactly
    assert ok.status_code == 202, ok.text
    big = await exec_api.create(member, wid, "€" * 17)  # 51 + 2 = 53
    assert big.status_code == 413
    assert jbody(big) == {"detail": "execution input exceeds max_bytes=50; got 53"}


async def test_create_invisible_workflow_404_exact(
    member: Member,
    second_member: Member,
    exec_api: ExecApi,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    private = await db.seed_workflow(second_member, is_public=False)
    r = await exec_api.create(member, private)
    assert r.status_code == 404
    assert jbody(r) == {"detail": f"Workflow {private!r} not found."}
    r = await exec_api.create(member, "no-such-workflow")
    assert r.status_code == 404
    assert jbody(r) == {"detail": "Workflow 'no-such-workflow' not found."}
    assert await db.executions_of(private) == [] and rec.calls == []


async def test_create_allowed_for_owner_public_assignee_and_admin(
    member: Member,
    second_member: Member,
    third_member: Member,
    admin: Member,
    exec_api: ExecApi,
    db: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = install_enqueue(monkeypatch, EnqueueRecorder())
    private = await db.seed_workflow(second_member, is_public=False)
    public = await db.seed_workflow(second_member, is_public=True)
    await db.assign(private, member.id, second_member.id)

    expected: list[tuple[Member, str]] = [
        (second_member, private),  # owner
        (member, private),  # assignee
        (member, public),  # public
        (admin, private),  # admin
    ]
    ids: list[str] = []
    for who, wid in expected:
        r = await exec_api.create(who, wid)
        assert r.status_code == 202, r.text
        assert jbody(r)["userId"] == who.id and jbody(r)["workflowId"] == wid
        ids.append(jbody(r)["id"])
    # an unassigned third user is refused on the private one
    assert (await exec_api.create(third_member, private)).status_code == 404
    assert [c[0] for c in rec.calls] == ids


async def test_create_enqueue_failure_marks_row_failed_and_returns_500(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_enqueue(monkeypatch, EnqueueRecorder(raises=RuntimeError("boom")))
    wid = await db.seed_workflow(member)
    bystander = await db.seed_execution(wid, member, status="queued")
    before_bystander = db.snapshot(await db.must(bystander))

    r = await exec_api.create(member, wid)
    assert r.status_code == 500, r.text
    created = [e for e in await db.executions_of(wid) if e.id != bystander]
    assert len(created) == 1
    row = created[0]
    assert jbody(r) == {"detail": f"Execution {row.id!r} could not be enqueued for processing."}
    assert row.status == "failed"
    assert row.error == "failed to enqueue execution for durable processing: boom"
    assert row.completed_at is not None and row.completed_at > ago(minutes=1)
    assert row.failure_code is None
    assert row.lease_owner is None and row.delivery_attempts == 0
    assert db.snapshot(await db.must(bystander)) == before_bystander


async def test_create_enqueue_failure_after_concurrent_cancel_keeps_canceled(
    member: Member, exec_api: ExecApi, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G1 guard: the failed-write is conditional on status='queued'."""
    wid = await db.seed_workflow(member)
    state: dict[str, Any] = {}

    async def _cancel_then_fail(execution_id: str) -> None:
        await db.set_execution(
            execution_id,
            status="canceled",
            error="Execution canceled by user request.",
            completed_at=ago(seconds=5),
        )
        state["after_cancel"] = db.snapshot(await db.must(execution_id))

    install_enqueue(
        monkeypatch, EnqueueRecorder(before=_cancel_then_fail, raises=RuntimeError("late"))
    )
    r = await exec_api.create(member, wid)
    assert r.status_code == 500, r.text
    (row,) = await db.executions_of(wid)
    assert jbody(r)["detail"] == f"Execution {row.id!r} could not be enqueued for processing."
    assert row.status == "canceled"
    assert db.snapshot(row) == state["after_cancel"]  # untouched by the failure handler
