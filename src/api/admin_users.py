"""Admin-only endpoints for user management (Phase 10d)."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.security.auth import ensure_admin
from src.storage.db import get_db

router = APIRouter(prefix="/admin", tags=["admin-users"])


class RoleUpdateRequest(BaseModel):
    role: Literal["admin", "member"]


class UserSummary(BaseModel):
    id: str
    email: str
    role: str
    display_name: str | None = Field(default=None, alias="displayName")

    class Config:
        populate_by_name = True
        from_attributes = True


@router.get("/users", response_model=list[UserSummary])
async def list_users(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> list[UserSummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await db.user.find_many(order={"email": "asc"})  # pyright: ignore[reportAttributeAccessIssue]
    out: list[UserSummary] = []
    for r in rows:
        role_val = r.role.value if hasattr(r.role, "value") else str(r.role)
        out.append(UserSummary(id=r.id, email=r.email, role=role_val, displayName=r.displayName))
    return out


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
        where={"id": user_id}, data={"role": payload.role}
    )
    role_val = updated.role.value if hasattr(updated.role, "value") else str(updated.role)
    return UserSummary(
        id=updated.id, email=updated.email, role=role_val, displayName=updated.displayName
    )


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


__all__ = ["router"]
