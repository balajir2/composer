"""Idempotency-key helpers shared by POST /executions and POST /api/run/{slug}."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]


async def find_execution_by_idempotency_key(
    db: Prisma,  # pyright: ignore[reportUnknownParameterType]
    workflow_id: str,
    user_id: str,
    idempotency_key: str,
) -> Any:
    """The caller's own execution for this key, or None.

    Keys are unique per (workflow, user) (Alembic 0002), so another user's use of the same
    key is invisible here. Prisma's frozen schema still declares the old
    (workflowId, idempotencyKey) compound unique, so this must not use find_unique on it.
    """
    return await db.workflowexecution.find_first(  # pyright: ignore[reportAttributeAccessIssue]
        where={  # pyright: ignore[reportArgumentType]
            "workflowId": workflow_id,
            "userId": user_id,
            "idempotencyKey": idempotency_key,
        }
    )


def require_same_caller(existing: Any, user_id: str) -> None:
    """Fail-closed backstop. The lookup is already scoped to the caller, so a row owned by
    someone else should be unreachable; if a regression ever returns one, refuse with a 409
    that says nothing about it instead of replaying another user's execution."""
    if getattr(existing, "userId", None) != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency key already used for this workflow",
        )
