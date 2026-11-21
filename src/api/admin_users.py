"""Admin-only endpoints for user management (Phase 10d)."""

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import UserRole
from src.db.repos import api_keys as keys_repo
from src.db.repos import users as users_repo
from src.db.session import get_session
from src.db.types import utcnow
from src.security.auth import ensure_admin
from src.security.passwords import hash_password

router = APIRouter(prefix="/admin", tags=["admin-users"])


class RoleUpdateRequest(BaseModel):
    role: Literal["admin", "member"]


class ResetPasswordResponse(BaseModel):
    temporary_password: str = Field(alias="temporaryPassword")

    model_config = ConfigDict(populate_by_name=True)


class UserSummary(BaseModel):
    id: str
    email: str
    role: str
    display_name: str | None = Field(default=None, alias="displayName")
    is_active: bool = Field(default=True, alias="isActive")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


def _summary_from_row(row: users_repo.UserRecord) -> UserSummary:
    return UserSummary(
        id=row.id,
        email=row.email,
        role=row.role.value,
        displayName=row.display_name,
        isActive=row.is_active,
    )


@router.get("/users", response_model=list[UserSummary])
async def list_users(
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> list[UserSummary]:  # pyright: ignore[reportUnusedFunction]
    return [_summary_from_row(r) for r in await users_repo.list_all(session)]


@router.post("/users/{user_id}/role", response_model=UserSummary)
async def update_user_role(
    user_id: str,
    payload: RoleUpdateRequest,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> UserSummary:  # pyright: ignore[reportUnusedFunction]
    updated = await users_repo.set_role(session, user_id, UserRole(payload.role))
    if updated is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    await session.commit()
    return _summary_from_row(updated)


@router.delete("/users/{user_id}", response_model=UserSummary)
async def deactivate_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> UserSummary:  # pyright: ignore[reportUnusedFunction]
    """Soft-delete: mark inactive + revoke all API keys.

    Preserves audit trail (workflows, executions, approvals keep their
    userId).  Use POST /users/{id}/reactivate to restore access.
    """
    if await users_repo.get_by_id(session, user_id) is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    # One transaction: revoking keys and deactivating commit together.
    await keys_repo.revoke_all_active_for_user(session, user_id, utcnow())
    updated = await users_repo.set_active(session, user_id, False)
    if updated is None:  # deleted concurrently
        await session.rollback()
        raise HTTPException(404, f"user {user_id!r} not found")
    await session.commit()
    return _summary_from_row(updated)


@router.post("/users/{user_id}/reactivate", response_model=UserSummary)
async def reactivate_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> UserSummary:  # pyright: ignore[reportUnusedFunction]
    updated = await users_repo.set_active(session, user_id, True)
    if updated is None:
        raise HTTPException(404, f"user {user_id!r} not found")
    await session.commit()
    return _summary_from_row(updated)


@router.post("/users/{user_id}/api-keys/{key_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def admin_revoke_api_key(
    user_id: str,
    key_id: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> None:  # pyright: ignore[reportUnusedFunction]
    row = await keys_repo.get(session, key_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, f"API key {key_id!r} not found for user {user_id!r}")
    # Already revoked: no-op (revoked_at is never re-stamped).
    if await keys_repo.revoke_if_active(session, key_id, utcnow()):
        await session.commit()


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def admin_reset_password(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> ResetPasswordResponse:  # pyright: ignore[reportUnusedFunction]
    """Admin-forced reset: generate a temp password, force a change on next
    login. The plaintext temp password is returned exactly once — it is
    never stored or logged in plaintext.
    """
    temp_password = secrets.token_urlsafe(12)
    changed = await users_repo.set_password(
        session, user_id, hash_password(temp_password), must_change=True
    )
    if not changed:
        raise HTTPException(404, f"user {user_id!r} not found")
    await session.commit()
    return ResetPasswordResponse(temporaryPassword=temp_password)


__all__ = ["router"]
