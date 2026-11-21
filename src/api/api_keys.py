"""User API key management (Phase 10a).

Routes:
  POST   /api-keys         — create; returns plaintext once.
  GET    /api-keys         — list caller's keys (no plaintext).
  DELETE /api-keys/{id}    — revoke (soft-delete via revoked_at).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.repos import api_keys as keys_repo
from src.db.session import get_session
from src.db.types import ensure_utc, utcnow
from src.security.api_keys import extract_prefix, generate_api_key, hash_api_key
from src.security.auth import get_current_user_id

router = APIRouter(tags=["api-keys"])


class ApiKeyCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True)


class ApiKeyCreateResponse(BaseModel):
    id: str
    label: str
    key: str
    key_prefix: str = Field(..., alias="keyPrefix")
    created_at: datetime = Field(..., alias="createdAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True)


class ApiKeySummary(BaseModel):
    id: str
    label: str
    key_prefix: str = Field(..., alias="keyPrefix")
    created_at: datetime = Field(..., alias="createdAt")
    last_used_at: datetime | None = Field(default=None, alias="lastUsedAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    revoked_at: datetime | None = Field(default=None, alias="revokedAt")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> ApiKeyCreateResponse:  # pyright: ignore[reportUnusedFunction]
    settings = get_settings()
    plaintext = generate_api_key()
    prefix = extract_prefix(plaintext)
    hashed = hash_api_key(plaintext, rounds=settings.bcrypt_rounds)
    row = await keys_repo.create(
        session,
        user_id=user_id,
        label=payload.label,
        key_hash=hashed,
        key_prefix=prefix,
        expires_at=ensure_utc(payload.expires_at) if payload.expires_at else None,
    )
    await session.commit()
    return ApiKeyCreateResponse(
        id=row.id,
        label=row.label,
        key=plaintext,
        keyPrefix=row.key_prefix,
        createdAt=row.created_at,
        expiresAt=row.expires_at,
    )


@router.get("/api-keys", response_model=list[ApiKeySummary])
async def list_api_keys(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> list[ApiKeySummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await keys_repo.list_for_user(session, user_id)
    return [ApiKeySummary.model_validate(r) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> None:  # pyright: ignore[reportUnusedFunction]
    row = await keys_repo.get(session, key_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"API key {key_id!r} not found."
        )
    # Already revoked: idempotent no-op (revoked_at is never re-stamped).
    if await keys_repo.revoke_if_active(session, key_id, utcnow()):
        await session.commit()


__all__ = ["router"]
