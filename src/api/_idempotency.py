"""Idempotency-key helpers shared by POST /executions and POST /api/run/{slug}."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]


async def find_execution_by_idempotency_key(
    db: Prisma,  # pyright: ignore[reportUnknownParameterType]
    workflow_id: str,
    idempotency_key: str,
) -> Any:
    where: dict[str, Any] = {
        "workflowId_idempotencyKey": {
            "workflowId": workflow_id,
            "idempotencyKey": idempotency_key,
        }
    }
    return await db.workflowexecution.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where=where  # pyright: ignore[reportArgumentType]
    )


def require_same_caller(existing: Any, user_id: str) -> None:
    """Replay is for the original caller retrying. The key is unique per workflow, not per
    user, so on a shared workflow anyone else (admins included) gets a 409 that says
    nothing about the other run."""
    if getattr(existing, "userId", None) != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency key already used for this workflow",
        )
