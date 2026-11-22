import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists, ReferencedRowMissing
from src.db.models import User, UserRole, Workflow, WorkflowAssignment
from src.db.repos import workflow_assignments as repo

pytestmark = pytest.mark.integration


async def test_exists(session: AsyncSession) -> None:
    uid = "u" + secrets.token_hex(4)
    session.add(User(id=uid, email=f"{uid}@example.com", password_hash="x", role=UserRole.MEMBER))
    w = Workflow(name="W", nodes=[], edges=[])
    session.add(w)
    await session.flush()
    assert await repo.exists(session, w.id, uid) is False
    session.add(WorkflowAssignment(workflow_id=w.id, user_id=uid, assigned_by_id=uid))
    await session.flush()
    assert await repo.exists(session, w.id, uid) is True
    assert await repo.exists(session, w.id, "someone-else") is False
    assert await repo.exists(session, "missing", uid) is False


async def _wf(session: AsyncSession) -> str:
    w = Workflow(name="W", nodes=[], edges=[])
    session.add(w)
    await session.flush()
    return w.id


async def test_create_and_list_for_workflow(session: AsyncSession) -> None:
    wid, other = await _wf(session), await _wf(session)
    rec = await repo.create(session, workflow_id=wid, user_id="a", assigned_by_id="o")
    await repo.create(session, workflow_id=other, user_id="a", assigned_by_id="o")
    assert rec.workflow_id == wid and rec.user_id == "a" and rec.assigned_by_id == "o"
    assert rec.id and rec.assigned_at is not None
    listed = await repo.list_for_workflow(session, wid)
    assert [r.id for r in listed] == [rec.id]
    assert await repo.list_for_workflow(session, "missing") == []


async def test_create_duplicate_raises_and_session_stays_usable(session: AsyncSession) -> None:
    wid = await _wf(session)
    await repo.create(session, workflow_id=wid, user_id="a", assigned_by_id="o")
    with pytest.raises(AlreadyExists) as exc:
        await repo.create(session, workflow_id=wid, user_id="a", assigned_by_id="o")
    assert exc.value.constraint == "workflow_assignments_workflow_id_user_id_key"
    await repo.create(session, workflow_id=wid, user_id="b", assigned_by_id="o")
    assert len(await repo.list_for_workflow(session, wid)) == 2


async def test_create_for_a_missing_workflow_raises_and_session_stays_usable(
    session: AsyncSession,
) -> None:
    with pytest.raises(ReferencedRowMissing) as exc:
        await repo.create(session, workflow_id="gone", user_id="a", assigned_by_id="o")
    assert exc.value.constraint == "workflow_assignments_workflow_id_fkey"
    wid = await _wf(session)
    await repo.create(session, workflow_id=wid, user_id="a", assigned_by_id="o")
    assert await repo.exists(session, wid, "a") is True


async def test_delete(session: AsyncSession) -> None:
    wid = await _wf(session)
    await repo.create(session, workflow_id=wid, user_id="a", assigned_by_id="o")
    assert await repo.delete(session, wid, "a") is True
    assert await repo.delete(session, wid, "a") is False
    assert await repo.exists(session, wid, "a") is False


async def test_delete_involving_user_covers_both_columns(session: AsyncSession) -> None:
    w1, w2, w3 = await _wf(session), await _wf(session), await _wf(session)
    await repo.create(session, workflow_id=w1, user_id="x", assigned_by_id="o")
    await repo.create(session, workflow_id=w2, user_id="y", assigned_by_id="x")
    await repo.create(session, workflow_id=w3, user_id="y", assigned_by_id="o")
    assert await repo.delete_involving_user(session, "x") == 2
    assert await repo.delete_involving_user(session, "x") == 0
    assert [r.user_id for r in await repo.list_for_workflow(session, w3)] == ["y"]
