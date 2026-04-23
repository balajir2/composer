"""Auth endpoints common to both standalone and embedded deployment modes.

Currently /auth/me and /auth/sso-exchange.  Registered in both modes.

See Phase 7a spec §8.2, Phase 10a spec §4.4.
"""

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt as jose_jwt  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.api.auth_standalone import TokenPairResponse
from src.config import Settings, get_settings
from src.security.auth import AuthError, get_current_user_id
from src.security.jwt import create_access_token, create_refresh_token
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


class SsoExchangeRequest(BaseModel):
    azure_token: str = Field(..., alias="azureToken")

    model_config = ConfigDict(populate_by_name=True)


@router.post("/auth/sso-exchange", response_model=TokenPairResponse)
async def sso_exchange(
    payload: SsoExchangeRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> TokenPairResponse:  # pyright: ignore[reportUnusedFunction]
    settings = get_settings()
    if not settings.sso_enabled:
        raise HTTPException(400, "SSO is disabled")
    if not (settings.sso_azure_ad_tenant_id and settings.sso_azure_ad_expected_audience):
        raise HTTPException(500, "SSO misconfigured on server")

    from src.security.sso_azure import verify_azure_jwt

    claims = await verify_azure_jwt(
        payload.azure_token,
        tenant_id=settings.sso_azure_ad_tenant_id,
        expected_audience=settings.sso_azure_ad_expected_audience,
    )

    email_raw = claims.get("email") or claims.get("preferred_username")
    if not email_raw:
        raise AuthError("Azure JWT missing email claim")
    email = str(email_raw).lower()
    display_name = claims.get("name") or email

    # Look up or auto-provision.
    user = await db.user.find_unique(where={"email": email})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        user = await db.user.create(  # pyright: ignore[reportAttributeAccessIssue]
            data={
                "email": email,
                "passwordHash": None,
                "displayName": display_name,
            }
        )

    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    now = int(time.time())
    return TokenPairResponse(
        accessToken=access,
        refreshToken=refresh,
        accessTokenExpiresAt=now + settings.jwt_access_ttl_seconds,
        refreshTokenExpiresAt=now + settings.jwt_refresh_ttl_seconds,
    )


__all__ = ["EmbeddedMeResponse", "SsoExchangeRequest", "StandaloneMeResponse", "router"]
