"""Idempotency-key helpers shared by POST /executions and POST /api/run/{slug}."""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repos import executions as executions_repo
from src.db.repos.executions import ExecutionRecord


async def find_execution_by_idempotency_key(
    session: AsyncSession,
    workflow_id: str,
    user_id: str,
    idempotency_key: str,
) -> ExecutionRecord | None:
    """The caller's own execution for this key, or None.

    Keys are unique per (workflow, user) (Alembic 0002), so another user's use of the same
    key is invisible here.
    """
    return await executions_repo.find_by_idempotency_key(
        session, workflow_id=workflow_id, user_id=user_id, key=idempotency_key
    )


def require_same_caller(existing: ExecutionRecord, user_id: str) -> None:
    """Fail-closed backstop. The lookup is already scoped to the caller, so a row owned by
    someone else should be unreachable; if a regression ever returns one, refuse with a 409
    that says nothing about it instead of replaying another user's execution."""
    if existing.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency key already used for this workflow",
        )
