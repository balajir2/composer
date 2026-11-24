import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.db.errors import AlreadyExists, StalePrecondition
from src.db.models import User, UserRole, Workflow, WorkflowAssignment, WorkflowExecution
from src.db.repos import workflows as repo
from src.db.repos.workflows import Visibility, WorkflowValues

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _tag() -> str:
    return secrets.token_hex(4)


async def _user(session: AsyncSession) -> str:
    uid = "u" + _tag()
    session.add(User(id=uid, email=f"{uid}@example.com", password_hash="x", role=UserRole.MEMBER))
    await session.flush()
    return uid


def _values(**over: Any) -> WorkflowValues:
    base: dict[str, Any] = {"name": "W", "nodes": [], "edges": []}
    base.update(over)
    return cast("WorkflowValues", base)


def _viewer(user_id: str, admin: bool = False) -> Visibility:
    return {"user_id": user_id, "is_admin": admin}


async def _assign(session: AsyncSession, wid: str, uid: str, by: str) -> None:
    session.add(WorkflowAssignment(workflow_id=wid, user_id=uid, assigned_by_id=by))
    await session.flush()


async def _page(session: AsyncSession, viewer: Visibility, **over: Any) -> Any:
    args: dict[str, Any] = {
        "mine": False,
        "is_template": None,
        "is_public": None,
        "category": None,
        "limit": 100,
        "offset": 0,
    }
    args.update(over)
    return await repo.list_page(session, viewer=viewer, **args)


async def test_create_get_defaults_and_json_round_trip(session: AsyncSession) -> None:
    nodes = [{"id": "a", "type": "start", "data": {"k": [1, None, {"x": "y"}]}}]
    edges = [{"id": "e", "source": "a", "target": "b"}]
    w = await repo.create(session, _values(nodes=nodes, edges=edges, tags=["x", "y"]))
    assert w.id and w.is_public is False and w.is_production is False and w.is_template is False
    assert w.nodes == nodes and w.edges == edges and w.tags == ["x", "y"]
    assert await repo.get(session, w.id) == w
    assert await repo.get(session, "missing") is None


async def test_tags_null_is_empty_list(session: AsyncSession) -> None:
    w = await repo.create(session, _values(tags=None))
    await session.execute(text("UPDATE workflows SET tags = NULL WHERE id = :i"), {"i": w.id})
    session.expire_all()
    got = await repo.get(session, w.id)
    assert got is not None and got.tags == []


async def test_get_for_update_returns_the_row(session: AsyncSession) -> None:
    created = await repo.create(session, _values(name="Locked"))
    locked = await repo.get(session, created.id, for_update=True)
    assert locked is not None and locked.name == "Locked"
    assert await repo.get(session, "missing", for_update=True) is None


async def test_get_for_update_refreshes_an_instance_already_in_the_session(
    engine: AsyncEngine,
) -> None:
    """SELECT ... FOR UPDATE must not hand back stale attributes from the identity map."""
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        old, new = await _user(setup), await _user(setup)
        w = await repo.create(setup, _values(user_id=old))
        await setup.commit()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as first:
            before = await repo.get(first, w.id)
            assert before is not None and before.user_id == old
            async with AsyncSession(engine) as other:
                await other.execute(
                    text("UPDATE workflows SET user_id = :u WHERE id = :i"), {"u": new, "i": w.id}
                )
                await other.commit()
            locked = await repo.get(first, w.id, for_update=True)
            assert locked is not None and locked.user_id == new
            await first.rollback()
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(text("DELETE FROM workflows WHERE id = :i"), {"i": w.id})
            await cleanup.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": old, "b": new}
            )
            await cleanup.commit()


async def test_get_by_slug(session: AsyncSession) -> None:
    slug = "s-" + _tag()
    w = await repo.create(session, _values(external_slug=slug))
    assert (await repo.get_by_slug(session, slug)) == w
    assert await repo.get_by_slug(session, "nope-" + _tag()) is None


async def test_list_visibility_with_exists_and_multiple_assignees(session: AsyncSession) -> None:
    me, other, third = await _user(session), await _user(session), await _user(session)
    cat = "c" + _tag()
    own = await repo.create(session, _values(user_id=me, category=cat))
    pub = await repo.create(session, _values(user_id=other, is_public=True, category=cat))
    shared = await repo.create(session, _values(user_id=other, category=cat))
    await _assign(session, shared.id, me, other)
    await _assign(session, shared.id, third, other)  # several assignees: no duplicate rows
    priv = await repo.create(session, _values(user_id=other, category=cat))

    rows, total = await _page(session, _viewer(me), category=cat)
    ids = [r.id for r in rows]
    assert sorted(ids) == sorted([own.id, pub.id, shared.id]) and total == 3
    assert priv.id not in ids

    rows, total = await _page(session, _viewer(me), mine=True, category=cat)
    assert {r.id for r in rows} == {own.id, shared.id} and total == 2

    rows, total = await _page(session, _viewer(me, admin=True), category=cat)
    assert len(rows) == 4 and total == 4
    # admin + mine is still "mine"
    rows, total = await _page(session, _viewer(me, admin=True), mine=True, category=cat)
    assert {r.id for r in rows} == {own.id, shared.id} and total == 2


async def test_list_filters(session: AsyncSession) -> None:
    me = await _user(session)
    cat = "c" + _tag()
    a = await repo.create(session, _values(user_id=me, category=cat, is_template=True))
    b = await repo.create(session, _values(user_id=me, category=cat, is_public=True))
    v = _viewer(me)
    rows, _ = await _page(session, v, category=cat, is_template=True)
    assert [r.id for r in rows] == [a.id]
    rows, _ = await _page(session, v, category=cat, is_template=False)
    assert [r.id for r in rows] == [b.id]
    rows, _ = await _page(session, v, category=cat, is_public=True)
    assert [r.id for r in rows] == [b.id]
    rows, total = await _page(session, v, category=cat + "x")
    assert rows == [] and total == 0


async def test_pagination_total_and_ordering(session: AsyncSession) -> None:
    me = await _user(session)
    # created out of order so only ORDER BY can produce newest-first
    made = [
        await repo.create(session, _values(user_id=me, updated_at=T0 + timedelta(days=d)))
        for d in (2, 0, 3, 1)
    ]
    expected = [made[2].id, made[0].id, made[3].id, made[1].id]
    rows, total = await _page(session, _viewer(me), limit=100)
    assert [r.id for r in rows] == expected and total == 4
    rows, total = await _page(session, _viewer(me), limit=2, offset=1)
    assert [r.id for r in rows] == expected[1:3] and total == 4
    rows, total = await _page(session, _viewer(me), limit=2, offset=10)
    assert rows == [] and total == 4


async def test_search_visibility_fields_and_wildcards(session: AsyncSession) -> None:
    me, other, third = await _user(session), await _user(session), await _user(session)
    key = "Zq" + _tag()
    by_name = await repo.create(session, _values(user_id=me, name=f"has {key.lower()} inside"))
    by_desc = await repo.create(session, _values(user_id=other, is_public=True, description=key))
    shared = await repo.create(session, _values(user_id=other, name=key))
    await _assign(session, shared.id, me, other)
    await _assign(session, shared.id, third, other)
    hidden = await repo.create(session, _values(user_id=other, name=key))
    in_tag = await repo.create(session, _values(user_id=me, tags=[key], category=key, name="plain"))

    rows, total = await repo.search(session, viewer=_viewer(me), q=key, limit=50)
    assert sorted(r.id for r in rows) == sorted([by_name.id, by_desc.id, shared.id]) and total == 3
    assert hidden.id not in {r.id for r in rows} and in_tag.id not in {r.id for r in rows}

    rows, total = await repo.search(session, viewer=_viewer(me, admin=True), q=key, limit=50)
    assert len(rows) == 4 and total == 4
    rows, total = await repo.search(session, viewer=_viewer(me, admin=True), q=key, limit=2)
    assert len(rows) == 2 and total == 4

    # wildcards in q are NOT escaped (pinned): "_" matches any one char, "%" any run
    w = await repo.create(session, _values(user_id=me, name="ab" + key + "cd"))
    rows, _ = await repo.search(session, viewer=_viewer(me), q=f"b_{key[1:]}", limit=50)
    assert w.id in {r.id for r in rows}
    rows, _ = await repo.search(session, viewer=_viewer(me), q=f"ab%{key[:2]}", limit=50)
    assert w.id in {r.id for r in rows}


async def test_update_changes_only_given_keys_and_bumps_updated_at(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await repo.create(
        session, _values(description="keep", tags=["t"], nodes=[{"id": "n"}], updated_at=T0)
    )
    t = datetime(2026, 3, 4, 5, 6, 7, 8000, tzinfo=UTC)
    monkeypatch.setattr(repo, "utcnow", lambda: t)
    updated = await repo.update(session, w.id, {"name": "New", "is_public": True})
    assert updated is not None and updated.updated_at == t
    assert updated.name == "New" and updated.is_public is True
    assert updated.description == "keep" and updated.tags == ["t"]
    assert updated.nodes == [{"id": "n"}] and updated.created_at == w.created_at
    assert await repo.update(session, "missing", {"name": "x"}) is None
    assert await repo.get(session, w.id) == updated


async def test_update_with_a_matching_precondition_saves_and_bumps(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await repo.create(session, _values(updated_at=T0))
    t = datetime(2026, 3, 4, 5, 6, 7, 8000, tzinfo=UTC)
    monkeypatch.setattr(repo, "utcnow", lambda: t)
    updated = await repo.update(session, w.id, {"name": "New"}, expected_updated_at=T0)
    assert updated is not None and updated.name == "New" and updated.updated_at == t


async def test_update_with_a_stale_precondition_raises_and_writes_nothing(
    session: AsyncSession,
) -> None:
    w = await repo.create(session, _values(updated_at=T0))
    stale = T0 - timedelta(seconds=1)
    with pytest.raises(StalePrecondition) as info:
        await repo.update(session, w.id, {"name": "New"}, expected_updated_at=stale)
    assert info.value.current_updated_at == T0
    after = await repo.get(session, w.id)
    assert after is not None and after.name == "W" and after.updated_at == T0


async def test_update_with_a_precondition_on_a_missing_row_returns_none(
    session: AsyncSession,
) -> None:
    assert await repo.update(session, "missing", {"name": "x"}, expected_updated_at=T0) is None


async def test_precondition_compares_at_stored_millisecond_precision(
    session: AsyncSession,
) -> None:
    ms = datetime(2026, 11, 24, 10, 0, 0, 123000, tzinfo=UTC)
    w = await repo.create(session, _values(updated_at=ms))
    # A non-UTC offset still matches the same instant.
    echoed = datetime.fromisoformat("2026-11-24T11:00:00.123+01:00")
    updated = await repo.update(session, w.id, {"name": "New"}, expected_updated_at=echoed)
    assert updated is not None


async def test_sub_millisecond_precondition_is_the_callers_to_truncate(
    session: AsyncSession,
) -> None:
    """The bind rounds (``.123789`` becomes ``.124``), so the API must truncate first."""
    ms = datetime(2026, 11, 24, 10, 0, 0, 123000, tzinfo=UTC)
    w = await repo.create(session, _values(updated_at=ms))
    with pytest.raises(StalePrecondition):
        await repo.update(
            session, w.id, {"name": "x"}, expected_updated_at=ms.replace(microsecond=123789)
        )


async def test_update_refreshes_row_already_loaded_in_session(session: AsyncSession) -> None:
    w = await repo.create(session, _values())
    held = await session.get(Workflow, w.id)  # strong ref: the identity map is weak
    assert held is not None and held.name == "W"
    updated = await repo.update(session, w.id, {"name": "Z"})
    assert updated is not None and held.name == "Z"


async def test_update_slug_conflict_raises_with_constraint_and_session_survives(
    session: AsyncSession,
) -> None:
    slug = "s-" + _tag()
    await repo.create(session, _values(external_slug=slug))
    other = await repo.create(session, _values())
    with pytest.raises(AlreadyExists) as info:
        await repo.update(session, other.id, {"external_slug": slug, "is_production": True})
    assert info.value.constraint == "workflows_external_slug_key"
    assert "[SQL" not in str(info.value)
    again = await repo.update(session, other.id, {"name": "still works"})
    assert again is not None and again.name == "still works" and again.external_slug is None


async def test_delete_cascades_assignments_and_executions(session: AsyncSession) -> None:
    me = await _user(session)
    w = await repo.create(session, _values(user_id=me))
    keep = await repo.create(session, _values(user_id=me))
    await _assign(session, w.id, me, me)
    await _assign(session, keep.id, me, me)
    session.add(
        WorkflowExecution(
            workflow_id=w.id,
            status="completed",
            node_results={},
            variables={},
            thread_id="t" + _tag(),
        )
    )
    await session.flush()
    assert await repo.delete(session, w.id) is True
    assert await repo.delete(session, w.id) is False
    for table in ("workflow_assignments", "workflow_executions"):
        n = await session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE workflow_id = :i"), {"i": w.id}
        )
        assert n == 0
    kept = await session.scalar(
        text("SELECT count(*) FROM workflow_assignments WHERE workflow_id = :i"), {"i": keep.id}
    )
    assert kept == 1
    assert await repo.get(session, keep.id) is not None


async def test_list_production(session: AsyncSession) -> None:
    prod = await repo.create(session, _values(is_production=True, external_slug="p-" + _tag()))
    plain = await repo.create(session, _values())
    ids = {r.id for r in await repo.list_production(session)}
    assert prod.id in ids and plain.id not in ids


async def test_delete_for_user(session: AsyncSession) -> None:
    me, other = await _user(session), await _user(session)
    await repo.create(session, _values(user_id=me))
    await repo.create(session, _values(user_id=me))
    theirs = await repo.create(session, _values(user_id=other))
    assert await repo.delete_for_user(session, me) == 2
    assert await repo.delete_for_user(session, me) == 0
    assert await repo.get(session, theirs.id) is not None


async def test_claim_orphans_by_email_is_idempotent(session: AsyncSession) -> None:
    me, other = await _user(session), await _user(session)
    email = f"o{_tag()}@example.com"
    a = await repo.create(session, _values(original_owner_email=email, user_id=None))
    owned = await repo.create(session, _values(original_owner_email=email, user_id=other))
    elsewhere = await repo.create(session, _values(original_owner_email="x@y.z", user_id=None))
    assert await repo.claim_orphans_by_email(session, email, me) == 1
    assert await repo.claim_orphans_by_email(session, email, me) == 0
    got = await repo.get(session, a.id)
    assert got is not None and got.user_id == me
    owned_after = await repo.get(session, owned.id)
    assert owned_after is not None and owned_after.user_id == other
    e = await repo.get(session, elsewhere.id)
    assert e is not None and e.user_id is None


async def test_insert_if_absent(session: AsyncSession) -> None:
    wid = "w" + _tag()
    assert await repo.insert_if_absent(session, _values(id=wid, name="first")) is True
    assert await repo.insert_if_absent(session, _values(id=wid, name="second")) is False
    got = await repo.get(session, wid)
    assert got is not None and got.name == "first"
