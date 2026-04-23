"""LLM model catalog endpoints.

- `GET /llm-models?provider=<p>` — any authenticated user; returns enabled
  models for the given provider (used by the Designer's agent property
  panel to populate the model dropdown).
- Admin CRUD lives in `src/api/admin_llm_models.py`.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs this at runtime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from src.security.auth import get_current_user_id
from src.storage.db import get_db

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

router = APIRouter(tags=["llm-models"])


class LlmModelSummary(BaseModel):
    id: str
    provider: str
    model_id: str = Field(..., alias="modelId")
    label: str | None
    enabled: bool
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


@router.get("/llm-models", response_model=list[LlmModelSummary])
async def list_enabled_models(
    provider: str | None = Query(default=None),
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _user_id: str = Depends(get_current_user_id),
) -> list[LlmModelSummary]:  # pyright: ignore[reportUnusedFunction]
    where: dict[str, object] = {"enabled": True}
    if provider is not None:
        where["provider"] = provider
    rows = await db.llmmodel.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where,
        order=[{"provider": "asc"}, {"modelId": "asc"}],
    )
    return [LlmModelSummary.model_validate(r) for r in rows]


__all__ = ["LlmModelSummary", "router"]
