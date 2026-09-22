"""Admin-only CRUD for LLM API keys (Phase 9e).

Keys stored AES-256-GCM-encrypted via src/security/encryption.py.  Plaintext
is never returned over HTTP; only `provider` + `keyPrefix` (first 6 chars).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.api.admin_llm_keys_test import run_key_test
from src.config import get_settings
from src.security.auth import ensure_admin
from src.security.encryption import EncryptionError, decrypt, encrypt
from src.security.key_sync import PROVIDER_TO_SETTINGS_FIELD
from src.storage.db import get_db

router = APIRouter(prefix="/admin/llm-keys", tags=["admin-llm-keys"])

_ALLOWED_PROVIDERS = {
    "anthropic",
    "openai",
    "google",
    "groq",
    "deepseek",
    "qwen",
    "langsmith",
    "tavily",
    "firecrawl",
    "serper",
    "browserless",
    "gamma",
    "resend",
    "typesafe",
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
    # Immediately reflect the new value on the in-process Settings so the
    # next workflow that resolves settings.<provider>_api_key sees the
    # fresh key without a restart.  Other Cloud Run instances (when scaled
    # >1) still see the old value until their next boot; src/security/key_sync.py
    # picks it up via the lifespan sync.
    field = PROVIDER_TO_SETTINGS_FIELD.get(provider)
    if field is not None:
        setattr(get_settings(), field, payload.value)
    # update() returns Optional but `existing is None` was just checked,
    # so the row exists.  Pyright can't carry that across the await.
    return LlmKeySummary(
        provider=row.provider,  # pyright: ignore[reportOptionalMemberAccess]
        key_prefix=row.keyPrefix,  # pyright: ignore[reportOptionalMemberAccess]
        updated_at=str(row.updatedAt),  # pyright: ignore[reportOptionalMemberAccess]
    )


class LlmKeyTestResponse(BaseModel):
    ok: bool
    status: int | None = None
    message: str


@router.post("/{provider}/test-connection", response_model=LlmKeyTestResponse)
async def test_llm_key(
    provider: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> LlmKeyTestResponse:  # pyright: ignore[reportUnusedFunction]
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(422, f"unsupported provider {provider!r}")
    row = await db.llmapikey.find_unique(where={"provider": provider})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None:
        raise HTTPException(404, f"no key set for provider {provider!r}")
    try:
        key = decrypt(row.encryptedKey)
    except EncryptionError as exc:
        raise HTTPException(500, f"failed to decrypt key: {exc}") from exc
    result = await run_key_test(provider, key)
    return LlmKeyTestResponse(ok=result.ok, status=result.status, message=result.message)


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
    # Clear the in-process Settings value too — the admin clicked Delete,
    # so they want the key gone from the running app, not just from the DB.
    # If the same env var is also set in .env / Cloud Run, this clears it
    # only in-memory; the next boot will re-read env and the value
    # reappears (env-wins).  That's the right behaviour: deleting a DB row
    # shouldn't shadow an operator-set env var.
    field = PROVIDER_TO_SETTINGS_FIELD.get(provider)
    if field is not None:
        setattr(get_settings(), field, "")


__all__ = ["router"]
