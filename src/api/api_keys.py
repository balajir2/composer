"""User API key management (Phase 10a).

Routes:
  POST   /api-keys         — create; returns plaintext once.
  GET    /api-keys         — list caller's keys (no plaintext).
  DELETE /api-keys/{id}    — revoke (soft-delete via revoked_at).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.security.api_keys import extract_prefix, generate_api_key, hash_api_key
from src.security.auth import get_current_user_id
from src.storage.db import get_db

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
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> ApiKeyCreateResponse:  # pyright: ignore[reportUnusedFunction]
    settings = get_settings()
    plaintext = generate_api_key()
    prefix = extract_prefix(plaintext)
    hashed = hash_api_key(plaintext, rounds=settings.bcrypt_rounds)
    row = await db.apikey.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "userId": user_id,
            "label": payload.label,
            "keyHash": hashed,
            "keyPrefix": prefix,
            "expiresAt": payload.expires_at,
        }
    )
    return ApiKeyCreateResponse.model_validate(  # pyright: ignore[reportUnknownMemberType]
        {
            "id": row.id,  # pyright: ignore[reportUnknownMemberType]
            "label": row.label,  # pyright: ignore[reportUnknownMemberType]
            "key": plaintext,
            "keyPrefix": row.keyPrefix,  # pyright: ignore[reportUnknownMemberType]
            "createdAt": row.createdAt,  # pyright: ignore[reportUnknownMemberType]
            "expiresAt": row.expiresAt,  # pyright: ignore[reportUnknownMemberType]
        }
    )


@router.get("/api-keys", response_model=list[ApiKeySummary])
async def list_api_keys(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> list[ApiKeySummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await db.apikey.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"userId": user_id},
        order={"createdAt": "desc"},
    )
    return [ApiKeySummary.model_validate(r) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> None:  # pyright: ignore[reportUnusedFunction]
    row = await db.apikey.find_unique(where={"id": key_id})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None or row.userId != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"API key {key_id!r} not found."
        )
    if row.revokedAt is not None:
        return  # already revoked; idempotent
    await db.apikey.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": key_id}, data={"revokedAt": datetime.now(tz=UTC)}
    )


__all__ = ["router"]
