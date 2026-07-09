"""Admin-only endpoints for user management (Phase 10d)."""

import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.security.auth import ensure_admin
from src.security.passwords import hash_password
from src.storage.db import get_db

router = APIRouter(prefix="/admin", tags=["admin-users"])


class RoleUpdateRequest(BaseModel):
    role: Literal["admin", "member"]


class ResetPasswordResponse(BaseModel):
    temporary_password: str = Field(alias="temporaryPassword")

    class Config:
        populate_by_name = True


class UserSummary(BaseModel):
    id: str
    email: str
    role: str
    display_name: str | None = Field(default=None, alias="displayName")
    is_active: bool = Field(default=True, alias="isActive")

    class Config:
        populate_by_name = True
        from_attributes = True


def _summary_from_row(row: Any) -> "UserSummary":
    """Build a UserSummary from a Prisma User row, coercing the role enum."""
    role_attr = row.role
    role_val = (
        role_attr.value  # pyright: ignore[reportOptionalMemberAccess]
        if hasattr(role_attr, "value")
        else str(role_attr)
    )
    return UserSummary(
        id=row.id,
        email=row.email,
        role=role_val,
        displayName=row.displayName,
        isActive=getattr(row, "isActive", True),
    )


@router.get("/users", response_model=list[UserSummary])
async def list_users(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> list[UserSummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await db.user.find_many(order={"email": "asc"})  # pyright: ignore[reportAttributeAccessIssue]
    return [_summary_from_row(r) for r in rows]


@router.post("/users/{user_id}/role", response_model=UserSummary)
async def update_user_role(
    user_id: str,
    payload: RoleUpdateRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> UserSummary:  # pyright: ignore[reportUnusedFunction]
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    updated = await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id},
        data={"role": payload.role},  # pyright: ignore[reportArgumentType]
    )
    return _summary_from_row(updated)


@router.delete("/users/{user_id}", response_model=UserSummary)
async def deactivate_user(
    user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> UserSummary:  # pyright: ignore[reportUnusedFunction]
    """Soft-delete: mark inactive + revoke all API keys.

    Preserves audit trail (workflows, executions, approvals keep their
    userId).  Use POST /users/{id}/reactivate to restore access.
    """
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    # Revoke all non-revoked API keys.
    now = datetime.now(UTC)
    await db.apikey.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"userId": user_id, "revokedAt": None},
        data={"revokedAt": now},
    )
    updated = await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id}, data={"isActive": False}
    )
    return _summary_from_row(updated)


@router.post("/users/{user_id}/reactivate", response_model=UserSummary)
async def reactivate_user(
    user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> UserSummary:  # pyright: ignore[reportUnusedFunction]
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    updated = await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id}, data={"isActive": True}
    )
    return _summary_from_row(updated)


@router.post("/users/{user_id}/api-keys/{key_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def admin_revoke_api_key(
    user_id: str,
    key_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> None:  # pyright: ignore[reportUnusedFunction]
    row = await db.apikey.find_unique(where={"id": key_id})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None or row.userId != user_id:
        raise HTTPException(404, f"API key {key_id!r} not found for user {user_id!r}")
    if row.revokedAt is not None:
        return
    await db.apikey.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": key_id}, data={"revokedAt": datetime.now(UTC)}
    )


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def admin_reset_password(
    user_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> ResetPasswordResponse:  # pyright: ignore[reportUnusedFunction]
    """Admin-forced reset: generate a temp password, force a change on next
    login. The plaintext temp password is returned exactly once — it is
    never stored or logged in plaintext.
    """
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    temp_password = secrets.token_urlsafe(12)
    await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id},
        data={
            "passwordHash": hash_password(temp_password),
            "mustChangePassword": True,
        },
    )
    return ResetPasswordResponse(temporaryPassword=temp_password)


__all__ = ["router"]
