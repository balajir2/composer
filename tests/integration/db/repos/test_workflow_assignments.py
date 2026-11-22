import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
