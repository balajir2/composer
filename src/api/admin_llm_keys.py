"""Admin-only CRUD for LLM API keys (Phase 9e).

Keys stored AES-256-GCM-encrypted via src/security/encryption.py.  Plaintext
is never returned over HTTP; only `provider` + `keyPrefix` (first 6 chars).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.security.auth import ensure_admin
from src.security.encryption import encrypt
from src.storage.db import get_db

router = APIRouter(prefix="/admin/llm-keys", tags=["admin-llm-keys"])

_ALLOWED_PROVIDERS = {
    "anthropic",
    "openai",
    "google",
    "groq",
    "langsmith",
    "tavily",
    "firecrawl",
    "serper",
    "browserless",
    "gamma",
}


class LlmKeySummary(BaseModel):
    provider: str
    keyPrefix: str = Field(..., alias="key_prefix")
    updatedAt: str = Field(..., alias="updated_at")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LlmKeyUpsert(BaseModel):
    value: str = Field(..., min_length=1)


def _prefix(value: str) -> str:
    return value[:6]


@router.get("", response_model=list[LlmKeySummary])
async def list_llm_keys(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> list[LlmKeySummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await db.llmapikey.find_many(order={"provider": "asc"})  # pyright: ignore[reportAttributeAccessIssue]
    return [
        LlmKeySummary(provider=r.provider, key_prefix=r.keyPrefix, updated_at=str(r.updatedAt))
        for r in rows
    ]


@router.get("/{provider}", response_model=LlmKeySummary)
async def get_llm_key(
    provider: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> LlmKeySummary:  # pyright: ignore[reportUnusedFunction]
    row = await db.llmapikey.find_unique(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None:
        raise HTTPException(404, f"no key set for provider {provider!r}")
    return LlmKeySummary(
        provider=row.provider, key_prefix=row.keyPrefix, updated_at=str(row.updatedAt)
    )


@router.put("/{provider}", response_model=LlmKeySummary)
async def upsert_llm_key(
    provider: str,
    payload: LlmKeyUpsert,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> LlmKeySummary:  # pyright: ignore[reportUnusedFunction]
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(422, f"unsupported provider {provider!r}")
    encrypted = encrypt(payload.value)
    prefix = _prefix(payload.value)
    existing = await db.llmapikey.find_unique(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        row = await db.llmapikey.create(  # pyright: ignore[reportAttributeAccessIssue]
            data={"provider": provider, "encryptedKey": encrypted, "keyPrefix": prefix}
        )
    else:
        row = await db.llmapikey.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"provider": provider},
            data={"encryptedKey": encrypted, "keyPrefix": prefix},
        )
    return LlmKeySummary(
        provider=row.provider, key_prefix=row.keyPrefix, updated_at=str(row.updatedAt)
    )


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_key(
    provider: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> None:  # pyright: ignore[reportUnusedFunction]
    existing = await db.llmapikey.find_unique(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(404, f"no key set for provider {provider!r}")
    await db.llmapikey.delete(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]


__all__ = ["router"]
