"""Auth endpoints common to both standalone and embedded deployment modes.

Currently just /auth/me.  Registered in both modes.

See Phase 7a spec §8.2.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt as jose_jwt  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import Settings, get_settings
from src.security.auth import get_current_user_id
from src.storage.db import get_db

router = APIRouter(tags=["auth"])


class StandaloneMeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    email: str
    display_name: str | None = Field(default=None, alias="displayName")
    role: str


class EmbeddedMeResponse(BaseModel):
    id: str
    claims: dict[str, Any]


@router.get("/auth/me")
async def me(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> StandaloneMeResponse | EmbeddedMeResponse:  # pyright: ignore[reportUnusedFunction]
    if settings.deployment_mode == "standalone":
        user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
        return StandaloneMeResponse(
            id=user.id,
            email=user.email,
            displayName=getattr(user, "displayName", None),
            role=str(user.role),
        )

    # embedded: return JWT claims directly
    header = request.headers.get("authorization", "")
    token = header[len("bearer ") :].strip() if header.lower().startswith("bearer ") else ""
    claims: dict[str, Any] = {}
    if token and settings.iep_shared_secret:
        try:
            claims = jose_jwt.decode(token, settings.iep_shared_secret, algorithms=["HS256"])
        except Exception:
            # get_current_user_id already validated — if we're here with a
            # bad token, something is inconsistent; return empty claims
            claims = {}
    return EmbeddedMeResponse(id=user_id, claims=claims)


__all__ = ["EmbeddedMeResponse", "StandaloneMeResponse", "router"]
