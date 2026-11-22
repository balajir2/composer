"""Workflow assignments (which users may open and edit a workflow they don't own)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import WorkflowAssignment


async def exists(session: AsyncSession, workflow_id: str, user_id: str) -> bool:
    """True when ``user_id`` has an assignment on ``workflow_id``."""
    stmt = (
        select(WorkflowAssignment.id)
        .where(WorkflowAssignment.workflow_id == workflow_id, WorkflowAssignment.user_id == user_id)
        .limit(1)
    )
    return (await session.scalars(stmt)).first() is not None
