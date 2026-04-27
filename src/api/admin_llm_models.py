"""Admin CRUD for the LLM model catalog."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.admin_llm_models_verify import run_model_verify
from src.api.llm_models import LlmModelSummary
from src.security.auth import ensure_admin
from src.security.encryption import EncryptionError, decrypt
from src.storage.db import get_db

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

router = APIRouter(prefix="/admin/llm-models", tags=["admin-llm-models"])


class LlmModelCreate(BaseModel):
    provider: str
    model_id: str = Field(..., alias="modelId")
    label: str | None = None
    enabled: bool = True

    model_config = ConfigDict(populate_by_name=True)


class LlmModelUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None

    model_config = ConfigDict(populate_by_name=True)


@router.get("", response_model=list[LlmModelSummary])
async def admin_list_all_models(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> list[LlmModelSummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await db.llmmodel.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        order=[{"provider": "asc"}, {"modelId": "asc"}],
    )
    return [LlmModelSummary.model_validate(r) for r in rows]


@router.post("", response_model=LlmModelSummary, status_code=status.HTTP_201_CREATED)
async def create_llm_model(
    payload: LlmModelCreate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> LlmModelSummary:  # pyright: ignore[reportUnusedFunction]
    try:
        row = await db.llmmodel.create(  # pyright: ignore[reportAttributeAccessIssue]
            data={
                "provider": payload.provider,
                "modelId": payload.model_id,
                "label": payload.label,
                "enabled": payload.enabled,
            }
        )
    except Exception as exc:  # unique-constraint violation
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Model already exists for provider: {exc}",
        ) from exc
    return LlmModelSummary.model_validate(row)


@router.patch("/{model_id}", response_model=LlmModelSummary)
async def update_llm_model(
    model_id: str,
    payload: LlmModelUpdate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> LlmModelSummary:  # pyright: ignore[reportUnusedFunction]
    existing = await db.llmmodel.find_unique(where={"id": model_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id!r} not found.",
        )
    update_data: dict[str, object] = {}
    if payload.label is not None:
        update_data["label"] = payload.label
    if payload.enabled is not None:
        update_data["enabled"] = payload.enabled
    row = await db.llmmodel.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": model_id},
        data=update_data,  # pyright: ignore[reportArgumentType]
    )
    return LlmModelSummary.model_validate(row)


class LlmModelVerifyResponse(BaseModel):
    status: str  # "ok" | "unavailable" | "auth_error" | "error"
    http_status: int | None = None
    message: str
    verified_at: str
    auto_disabled: bool = (
        False  # True when probe returned "unavailable" and we flipped enabled=False
    )
    model: LlmModelSummary

    model_config = ConfigDict(populate_by_name=True)


@router.post("/{model_id}/verify", response_model=LlmModelVerifyResponse)
async def verify_llm_model(
    model_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> LlmModelVerifyResponse:  # pyright: ignore[reportUnusedFunction]
    """Probe the live provider with the actual model_id and stamp the row.

    Why this exists: provider /models listings include retired or
    grandfathered models; verify is the user-driven escape hatch that
    catches "shows up in dropdown but 404s on first call" cases like
    Google retiring `gemini-2.0-flash` for new keys.

    Auth errors (401/403) are NOT recorded against the model — they
    point at the API key, not the model.  Only `ok` / `unavailable`
    statuses get persisted to the row; `auth_error` / `error` return
    a clear message but leave the prior verification stamp intact.
    """
    row = await db.llmmodel.find_unique(where={"id": model_id})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id!r} not found.",
        )
    key_row = await db.llmapikey.find_unique(where={"provider": row.provider})  # pyright: ignore[reportAttributeAccessIssue]
    if key_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No API key configured for provider {row.provider!r}. "
                "Add it under Admin → LLM keys before verifying."
            ),
        )
    try:
        api_key = decrypt(key_row.encryptedKey)
    except EncryptionError as exc:
        raise HTTPException(500, f"failed to decrypt {row.provider} key: {exc}") from exc

    result = await run_model_verify(row.provider, row.modelId, api_key)
    now = datetime.now(UTC)

    # Only stamp the row when the probe actually says something about
    # the model.  Auth/network errors don't tell us whether the model
    # is invocable, so we leave the prior stamp untouched.
    #
    # On `unavailable` we ALSO flip enabled=False so the model stops
    # appearing in the Designer's dropdown.  The user complaint that
    # drove this feature was Google retiring `gemini-2.0-flash` while
    # leaving it in /models — flipping enabled is the action the admin
    # would take next anyway, so we save the second click.
    update_data: dict[str, object] = {}
    auto_disabled = False
    if result.status in ("ok", "unavailable"):
        update_data["verificationStatus"] = result.status
        update_data["verificationMessage"] = result.message
        update_data["verifiedAt"] = now
        if result.status == "unavailable" and row.enabled:
            update_data["enabled"] = False
            auto_disabled = True
    if update_data:
        updated = await db.llmmodel.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": model_id},
            data=update_data,  # pyright: ignore[reportArgumentType]
        )
    else:
        updated = row

    return LlmModelVerifyResponse(
        status=result.status,
        http_status=result.http_status,
        message=result.message,
        verified_at=now.isoformat(),
        auto_disabled=auto_disabled,
        model=LlmModelSummary.model_validate(updated),
    )


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_model(
    model_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> None:  # pyright: ignore[reportUnusedFunction]
    existing = await db.llmmodel.find_unique(where={"id": model_id})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id!r} not found.",
        )
    await db.llmmodel.delete(where={"id": model_id})  # pyright: ignore[reportAttributeAccessIssue]


__all__ = ["router"]
