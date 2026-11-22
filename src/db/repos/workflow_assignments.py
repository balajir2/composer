"""Workflow assignments (which users may open and edit a workflow they don't own)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_referenced_row_missing
from src.db.models import WorkflowAssignment


@dataclass(frozen=True)
class AssignmentRecord:
    id: str
    workflow_id: str
    user_id: str
    assigned_by_id: str
    assigned_at: datetime


def _record(r: WorkflowAssignment) -> AssignmentRecord:
    return AssignmentRecord(
        id=r.id,
        workflow_id=r.workflow_id,
        user_id=r.user_id,
        assigned_by_id=r.assigned_by_id,
        assigned_at=r.assigned_at,
    )


async def exists(session: AsyncSession, workflow_id: str, user_id: str) -> bool:
    """True when ``user_id`` has an assignment on ``workflow_id``."""
    stmt = (
        select(WorkflowAssignment.id)
        .where(WorkflowAssignment.workflow_id == workflow_id, WorkflowAssignment.user_id == user_id)
        .limit(1)
    )
    return (await session.scalars(stmt)).first() is not None


async def list_for_workflow(session: AsyncSession, workflow_id: str) -> list[AssignmentRecord]:
    """Every assignment on the workflow. Unordered, as the Prisma query was."""
    stmt = select(WorkflowAssignment).where(WorkflowAssignment.workflow_id == workflow_id)
    return [_record(r) for r in await session.scalars(stmt)]


async def create(
    session: AsyncSession, *, workflow_id: str, user_id: str, assigned_by_id: str
) -> AssignmentRecord:
    """Insert an assignment, leaving the session usable on failure.

    ``AlreadyExists`` on a duplicate; ``ReferencedRowMissing`` when the workflow or a
    user was deleted concurrently.
    """
    row = WorkflowAssignment(
        workflow_id=workflow_id, user_id=user_id, assigned_by_id=assigned_by_id
    )
    await add_or_referenced_row_missing(session, row)
    return _record(row)


async def delete(session: AsyncSession, workflow_id: str, user_id: str) -> bool:
    """Remove one assignment. False when there was none."""
    result = await session.execute(
        sa_delete(WorkflowAssignment)
        .where(WorkflowAssignment.workflow_id == workflow_id, WorkflowAssignment.user_id == user_id)
        .returning(WorkflowAssignment.id)
    )
    return result.first() is not None


async def delete_involving_user(session: AsyncSession, user_id: str) -> int:
    """Remove assignments granted to or by ``user_id``; returns how many."""
    result = await session.execute(
        sa_delete(WorkflowAssignment)
        .where(
            or_(WorkflowAssignment.user_id == user_id, WorkflowAssignment.assigned_by_id == user_id)
        )
        .returning(WorkflowAssignment.id)
    )
    return len(result.all())
