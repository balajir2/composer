"""Optional ``expectedUpdatedAt`` precondition on workflow and builder saves (real database).

Kept beside the phase 3 oracle but separate from it: the oracle tests never send the field and
describe the no-precondition behaviour, which these tests re-pin only where it matters.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

import src.api.builder as builder_module
from src.engine.workflow import Workflow as EngineWorkflow
from tests.integration.sqla_phase3.conftest import (
    HTTPX_MARK,
    Db,
    Member,
    WorkflowApi,
    chain,
    jbody,
    node,
    not_found,
)

pytestmark = [pytest.mark.integration, HTTPX_MARK]

CHANGED = "workflow changed since you loaded it"


async def seen_version(
    wf_api: WorkflowApi, db: Db, who: Member, workflow_id: str
) -> tuple[dict[str, Any], str]:
    """The workflow as a client loads it, from a row whose ``updated_at`` is well in the past
    so a later save cannot land in the same millisecond."""
    await db.backdate(workflow_id)
    got = jbody(await wf_api.get(who, workflow_id))
    return got, got["updatedAt"]


def edit(created: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"name": created["name"], "nodes": created["nodes"], "edges": created["edges"], **extra}


async def test_the_response_updated_at_round_trips_as_the_next_precondition(
    member: Member, wf_api: WorkflowApi
) -> None:
    created = await wf_api.create(member)
    first = await wf_api.put(
        member, created["id"], edit(created, name="one", expectedUpdatedAt=created["updatedAt"])
    )
    assert first.status_code == 200, first.text
    # Chain: the value the previous save returned is exactly what the next save must send.
    second = await wf_api.put(
        member,
        created["id"],
        edit(created, name="two", expectedUpdatedAt=jbody(first)["updatedAt"]),
    )
    assert second.status_code == 200, second.text
    assert jbody(second)["name"] == "two"
    # And so is the value a plain GET returns.
    got = jbody(await wf_api.get(member, created["id"]))
    third = await wf_api.put(
        member, created["id"], edit(created, name="three", expectedUpdatedAt=got["updatedAt"])
    )
    assert third.status_code == 200, third.text


async def test_a_stale_precondition_is_409_with_the_current_updated_at_and_writes_nothing(
    member: Member, second_member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    created = await wf_api.create(member)
    await db.assign(created["id"], second_member.id, member.id)
    _, seen = await seen_version(wf_api, db, member, created["id"])

    theirs = await wf_api.put(second_member, created["id"], edit(created, name="theirs"))
    assert theirs.status_code == 200

    mine = await wf_api.put(
        member, created["id"], edit(created, name="mine", expectedUpdatedAt=seen)
    )
    assert mine.status_code == 409, mine.text
    detail = mine.json()["detail"]
    assert detail["message"] == CHANGED
    assert detail["code"] == "workflow_changed"
    assert detail["currentUpdatedAt"] == jbody(theirs)["updatedAt"]
    assert (await db.must(created["id"])).name == "theirs"


async def test_without_a_precondition_the_last_save_still_wins(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    created = await wf_api.create(member)
    await db.backdate(created["id"])
    r = await wf_api.put(member, created["id"], edit(created, name="unconditional"))
    assert r.status_code == 200 and jbody(r)["name"] == "unconditional"


async def test_a_precondition_on_a_deleted_workflow_is_404(
    client: AsyncClient, member: Member, wf_api: WorkflowApi
) -> None:
    created = await wf_api.create(member)
    deleted = await client.delete(f"/workflows/{created['id']}", headers=member.headers)
    assert deleted.status_code == 204
    r = await wf_api.put(
        member, created["id"], edit(created, expectedUpdatedAt=created["updatedAt"])
    )
    assert r.status_code == 404 and r.json()["detail"] == not_found(created["id"])


async def test_two_concurrent_saves_with_the_same_precondition_one_wins_one_conflicts(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    created = await wf_api.create(member)
    _, seen = await seen_version(wf_api, db, member, created["id"])

    results = await asyncio.gather(
        *(
            wf_api.put(member, created["id"], edit(created, name=n, expectedUpdatedAt=seen))
            for n in ("a", "b")
        )
    )

    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r for r in results if r.status_code == 200)
    assert (await db.must(created["id"])).name == jbody(winner)["name"]


async def builder_post(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    who: Member,
    workflow_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    expected: str | None,
    during_turn: Any = None,
) -> Any:
    async def _turn(**kwargs: Any) -> tuple[list[dict[str, Any]], str, Any]:
        if during_turn is not None:
            await during_turn()
        current = kwargs["workflow"]
        updated = EngineWorkflow.model_validate(
            {"id": current.id, "name": current.name, "nodes": nodes, "edges": edges}
        )
        return [], "ok", updated

    monkeypatch.setattr(builder_module, "run_builder_turn", _turn)

    def _model(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(builder_module, "build_chat_model", _model)
    body: dict[str, Any] = {"message": "go"}
    if expected is not None:
        body["expectedUpdatedAt"] = expected
    return await app_client.post(f"/builder/{workflow_id}/message", json=body, headers=who.headers)


async def test_builder_save_with_the_matching_precondition_returns_the_new_updated_at(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    member: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    created = await wf_api.create(member)
    _, seen = await seen_version(wf_api, db, member, created["id"])
    nodes, edges = chain(node("t", "transform"))

    r = await builder_post(client, monkeypatch, member, created["id"], nodes, edges, expected=seen)

    assert r.status_code == 200, r.text
    new_stamp = r.json()["workflow"]["updatedAt"]
    assert new_stamp != seen
    # The response stamp chains: the next builder message may send it as its precondition.
    again = await builder_post(
        client, monkeypatch, member, created["id"], nodes, edges, expected=new_stamp
    )
    assert again.status_code == 200, again.text


async def test_builder_save_is_409_when_someone_saved_during_the_turn_and_nothing_is_written(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    member: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    created = await wf_api.create(member)
    _, seen = await seen_version(wf_api, db, member, created["id"])
    nodes, edges = chain(node("t", "transform"))

    async def someone_else_saves() -> None:
        r = await wf_api.put(member, created["id"], edit(created, name="theirs"))
        assert r.status_code == 200, r.text

    r = await builder_post(
        client,
        monkeypatch,
        member,
        created["id"],
        nodes,
        edges,
        expected=seen,
        during_turn=someone_else_saves,
    )

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["message"] == CHANGED
    row = await db.must(created["id"])
    assert row.name == "theirs"
    stored_ids = [n["id"] for n in row.nodes]  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportGeneralTypeIssues]
    assert stored_ids == [n["id"] for n in created["nodes"]]


async def test_builder_save_without_a_precondition_is_unchanged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, member: Member, wf_api: WorkflowApi
) -> None:
    created = await wf_api.create(member)
    nodes, edges = chain(node("t", "transform"))
    r = await builder_post(client, monkeypatch, member, created["id"], nodes, edges, expected=None)
    assert r.status_code == 200, r.text


async def test_a_sub_millisecond_precondition_matches_over_http(
    member: Member, wf_api: WorkflowApi, db: Db
) -> None:
    created = await wf_api.create(member)
    await db.set_workflow(
        created["id"], updated_at=datetime(2026, 11, 24, 10, 0, 0, 123000, tzinfo=UTC)
    )
    r = await wf_api.put(
        member, created["id"], edit(created, expectedUpdatedAt="2026-11-24T10:00:00.123789Z")
    )
    assert r.status_code == 200, r.text


async def test_a_stranger_with_a_stale_precondition_is_refused_without_a_conflict_body(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    member: Member,
    second_member: Member,
    wf_api: WorkflowApi,
    db: Db,
) -> None:
    created = await wf_api.create(member)
    _, seen = await seen_version(wf_api, db, member, created["id"])
    await wf_api.put(member, created["id"], edit(created, name="moved on"))

    put = await wf_api.put(second_member, created["id"], edit(created, expectedUpdatedAt=seen))
    assert put.status_code == 403 and "currentUpdatedAt" not in put.text

    nodes, edges = chain(node("t", "transform"))
    built = await builder_post(
        client, monkeypatch, second_member, created["id"], nodes, edges, expected=seen
    )
    assert built.status_code == 404 and "currentUpdatedAt" not in built.text


async def test_a_builder_message_accepts_the_updated_at_a_designer_save_just_returned(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    member: Member,
    wf_api: WorkflowApi,
) -> None:
    """The Designer's autosave chain (PUT -> PUT) followed by a builder send, with no backdating.

    Autosave answers each PUT with the stamp the next request must send, and the builder
    panel sends the last such stamp. Neither the PUT response nor a plain GET may disagree
    with what the builder's pre-check reads.
    """
    created = await wf_api.create(member)
    nodes, edges = chain(node("t", "transform"))
    stamp = created["updatedAt"]
    for name in ("one", "two", "three"):
        r = await wf_api.put(
            member, created["id"], edit(created, name=name, expectedUpdatedAt=stamp)
        )
        assert r.status_code == 200, r.text
        stamp = jbody(r)["updatedAt"]
        got = jbody(await wf_api.get(member, created["id"]))
        assert got["updatedAt"] == stamp

    built = await builder_post(
        client, monkeypatch, member, created["id"], nodes, edges, expected=stamp
    )
    assert built.status_code == 200, built.text
