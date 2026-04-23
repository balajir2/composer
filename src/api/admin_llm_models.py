"""Admin CRUD for the LLM model catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.llm_models import LlmModelSummary
from src.security.auth import ensure_admin
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
        data=update_data,
    )
    return LlmModelSummary.model_validate(row)


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
