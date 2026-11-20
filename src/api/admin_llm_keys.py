"""Admin-only CRUD for LLM API keys (Phase 9e).

Keys stored AES-256-GCM-encrypted via src/security/encryption.py.  Plaintext
is never returned over HTTP; only `provider` + `keyPrefix` (first 6 chars).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.admin_llm_keys_test import run_key_test
from src.config import get_settings
from src.db.repos import llm_api_keys as keys_repo
from src.db.session import get_session
from src.security.auth import ensure_admin
from src.security.encryption import EncryptionError, decrypt, encrypt
from src.security.key_sync import PROVIDER_TO_SETTINGS_FIELD

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
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> list[LlmKeySummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await keys_repo.list_keys(session)
    return [
        LlmKeySummary(provider=r.provider, key_prefix=r.key_prefix, updated_at=str(r.updated_at))
        for r in rows
    ]


@router.get("/{provider}", response_model=LlmKeySummary)
async def get_llm_key(
    provider: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> LlmKeySummary:  # pyright: ignore[reportUnusedFunction]
    row = await keys_repo.get(session, provider)
    if row is None:
        raise HTTPException(404, f"no key set for provider {provider!r}")
    return LlmKeySummary(
        provider=row.provider, key_prefix=row.key_prefix, updated_at=str(row.updated_at)
    )


@router.put("/{provider}", response_model=LlmKeySummary)
async def upsert_llm_key(
    provider: str,
    payload: LlmKeyUpsert,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> LlmKeySummary:  # pyright: ignore[reportUnusedFunction]
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(422, f"unsupported provider {provider!r}")
    encrypted = encrypt(payload.value)
    prefix = _prefix(payload.value)
    result = await keys_repo.upsert(session, provider, encrypted_key=encrypted, key_prefix=prefix)
    await session.commit()
    # Reflect the new value on the in-process Settings so the next workflow that
    # resolves settings.<provider>_api_key sees it without a restart. Other
    # Cloud Run instances (when scaled >1) keep the old value until their next
    # boot, when src/security/key_sync.py picks it up via the lifespan sync.
    # Set only after the commit: a failed commit must not leave the running app
    # using a key that was never saved.
    field = PROVIDER_TO_SETTINGS_FIELD.get(provider)
    if field is not None:
        setattr(get_settings(), field, payload.value)
    row = result.record
    return LlmKeySummary(
        provider=row.provider, key_prefix=row.key_prefix, updated_at=str(row.updated_at)
    )


class LlmKeyTestResponse(BaseModel):
    ok: bool
    status: int | None = None
    message: str


@router.post("/{provider}/test-connection", response_model=LlmKeyTestResponse)
async def test_llm_key(
    provider: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> LlmKeyTestResponse:  # pyright: ignore[reportUnusedFunction]
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(422, f"unsupported provider {provider!r}")
    row = await keys_repo.get(session, provider)
    if row is None:
        raise HTTPException(404, f"no key set for provider {provider!r}")
    # Release the pooled connection before the provider probe (network call).
    await session.rollback()
    try:
        key = decrypt(row.encrypted_key)
    except EncryptionError as exc:
        raise HTTPException(500, f"failed to decrypt key: {exc}") from exc
    result = await run_key_test(provider, key)
    return LlmKeyTestResponse(ok=result.ok, status=result.status, message=result.message)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_key(
    provider: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> None:  # pyright: ignore[reportUnusedFunction]
    if not await keys_repo.delete(session, provider):
        raise HTTPException(404, f"no key set for provider {provider!r}")
    await session.commit()
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
