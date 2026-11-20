"""Admin CRUD for the LLM model catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 — FastAPI resolves this at runtime

from src.api.admin_llm_models_verify import run_model_verify
from src.api.llm_models import LlmModelSummary
from src.db.errors import AlreadyExists
from src.db.repos import llm_models as models_repo
from src.db.repos.common import UNSET
from src.db.session import get_session
from src.db.types import utcnow
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
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> list[LlmModelSummary]:  # pyright: ignore[reportUnusedFunction]
    rows = await models_repo.list_models(session)
    return [LlmModelSummary.model_validate(r) for r in rows]


@router.post("", response_model=LlmModelSummary, status_code=status.HTTP_201_CREATED)
async def create_llm_model(
    payload: LlmModelCreate,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> LlmModelSummary:  # pyright: ignore[reportUnusedFunction]
    try:
        row = await models_repo.create(
            session,
            provider=payload.provider,
            model_id=payload.model_id,
            label=payload.label,
            enabled=payload.enabled,
        )
    except AlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Model already exists for provider: {exc}",
        ) from exc
    await session.commit()
    return LlmModelSummary.model_validate(row)


@router.patch("/{model_id}", response_model=LlmModelSummary)
async def update_llm_model(
    model_id: str,
    payload: LlmModelUpdate,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> LlmModelSummary:  # pyright: ignore[reportUnusedFunction]
    row = await models_repo.update(
        session,
        model_id,
        label=payload.label if payload.label is not None else UNSET,
        enabled=payload.enabled if payload.enabled is not None else UNSET,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id!r} not found.",
        )
    await session.commit()
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
    session: AsyncSession = Depends(get_session),
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
    row = await models_repo.get_by_id(session, model_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id!r} not found.",
        )
    # Release the pooled connection before the key lookup and the slow provider
    # probe; record_verification below autobegins a fresh transaction.
    await session.rollback()
    # TODO(task 5): the key lookup moves to the llm_api_keys repo.
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

    result = await run_model_verify(row.provider, row.model_id, api_key)
    now = utcnow()

    # Only stamp the row when the probe actually says something about
    # the model.  Auth/network errors don't tell us whether the model
    # is invocable, so we leave the prior stamp untouched.
    #
    # On `unavailable` we ALSO flip enabled=False so the model stops
    # appearing in the Designer's dropdown.  The user complaint that
    # drove this feature was Google retiring `gemini-2.0-flash` while
    # leaving it in /models — flipping enabled is the action the admin
    # would take next anyway, so we save the second click.
    auto_disabled = False
    updated = row
    if result.status in ("ok", "unavailable"):
        auto_disabled = result.status == "unavailable" and row.enabled
        recorded = await models_repo.record_verification(
            session,
            model_id,
            status=result.status,
            message=result.message,
            verified_at=now,
            disable=auto_disabled,
        )
        await session.commit()
        if recorded is not None:
            updated = recorded

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
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> None:  # pyright: ignore[reportUnusedFunction]
    if not await models_repo.delete(session, model_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_id!r} not found.",
        )
    await session.commit()


__all__ = ["router"]
