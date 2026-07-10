"""Standalone-mode /auth/* endpoints.

Registered only when settings.deployment_mode == 'standalone'.
In embedded mode the router isn't included, so these routes 404.

See Phase 7a spec §8.1.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.integrations.email.resend import send_password_reset_email
from src.security.auth import get_user_id_allow_password_change
from src.security.jwt import (
    TokenVerificationError,
    create_access_token,
    create_password_change_token,
    create_refresh_token,
    verify_password_change_token,
    verify_refresh_token,
)
from src.security.passwords import hash_password, verify_password
from src.security.rate_limit import (
    RateLimiter,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db

router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str | None = Field(default=None, alias="displayName")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_token: str = Field(alias="refreshToken")


class AuthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    email: str
    display_name: str | None = Field(default=None, alias="displayName")
    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    access_token_expires_at: int = Field(alias="accessTokenExpiresAt")
    refresh_token_expires_at: int = Field(alias="refreshTokenExpiresAt")


class TokenPairResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    access_token_expires_at: int = Field(alias="accessTokenExpiresAt")
    refresh_token_expires_at: int = Field(alias="refreshTokenExpiresAt")


class MustChangePasswordResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    must_change_password: bool = Field(default=True, alias="mustChangePassword")
    password_change_token: str = Field(alias="passwordChangeToken")


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_password: str = Field(alias="currentPassword")
    new_password: str = Field(min_length=8, alias="newPassword")


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: str
    new_password: str = Field(min_length=8, alias="newPassword")


def _issue_pair(user_id: str) -> tuple[str, str, int, int]:
    """Returns (access_token, refresh_token, access_exp, refresh_exp) where
    the `*_exp` values are unix epoch seconds."""
    import time

    settings = get_settings()
    now = int(time.time())
    return (
        create_access_token(user_id),
        create_refresh_token(user_id),
        now + settings.jwt_access_ttl_seconds,
        now + settings.jwt_refresh_ttl_seconds,
    )


@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> AuthResponse:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_register",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_register_per_minute),
    )
    existing = await db.user.find_unique(where={"email": str(payload.email)})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")

    data: dict[str, Any] = {
        "email": str(payload.email),
        "passwordHash": hash_password(payload.password),
    }
    if payload.display_name:
        data["displayName"] = payload.display_name

    user = await db.user.create(data=data)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]

    access, refresh, access_exp, refresh_exp = _issue_pair(user.id)
    return AuthResponse(
        id=user.id,
        email=user.email,
        displayName=getattr(user, "displayName", None),
        accessToken=access,
        refreshToken=refresh,
        accessTokenExpiresAt=access_exp,
        refreshTokenExpiresAt=refresh_exp,
    )


@router.post("/auth/login", response_model=TokenPairResponse | MustChangePasswordResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> TokenPairResponse | MustChangePasswordResponse:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_login",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_login_per_minute),
    )
    user = await db.user.find_unique(where={"email": str(payload.email)})  # pyright: ignore[reportAttributeAccessIssue]
    # Uniform 401: don't distinguish unknown-email from wrong-password.
    # SSO-only accounts have a null passwordHash and must reject password
    # logins with the same generic message as bad creds.
    if (
        user is None
        or user.passwordHash is None
        or not verify_password(payload.password, user.passwordHash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        )
    if getattr(user, "isActive", True) is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account is deactivated")
    if getattr(user, "mustChangePassword", False):
        return MustChangePasswordResponse(passwordChangeToken=create_password_change_token(user.id))
    access, refresh, access_exp, refresh_exp = _issue_pair(user.id)
    return TokenPairResponse(
        accessToken=access,
        refreshToken=refresh,
        accessTokenExpiresAt=access_exp,
        refreshTokenExpiresAt=refresh_exp,
    )


@router.post("/auth/refresh", response_model=TokenPairResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> TokenPairResponse:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_refresh",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_refresh_per_minute),
    )
    try:
        claims = verify_refresh_token(payload.refresh_token)
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid refresh token: {exc}",
        ) from exc

    # RefreshTokenPayload is a Pydantic model — access sub directly
    user_id = claims.sub
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token missing sub",
        )

    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user no longer exists"
        )
    if getattr(user, "isActive", True) is False:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account is deactivated"
        )
    if getattr(user, "mustChangePassword", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="password change required"
        )
    access, new_refresh, access_exp, refresh_exp = _issue_pair(user.id)
    return TokenPairResponse(
        accessToken=access,
        refreshToken=new_refresh,
        accessTokenExpiresAt=access_exp,
        refreshTokenExpiresAt=refresh_exp,
    )


@router.post("/auth/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect() -> None:  # pyright: ignore[reportUnusedFunction]
    """Client-side token drop.  Server-side revocation lands in Phase 7b."""
    return None


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_user_id_allow_password_change),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_change_password",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_change_password_per_minute),
    )
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if (
        user is None
        or user.passwordHash is None
        or not verify_password(payload.current_password, user.passwordHash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="current password is incorrect"
        )
    await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id},
        data={
            "passwordHash": hash_password(payload.new_password),
            "mustChangePassword": False,
        },
    )
    return None


@router.post("/auth/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_forgot_password",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_forgot_password_per_minute),
    )
    user = await db.user.find_unique(where={"email": str(payload.email)})  # pyright: ignore[reportAttributeAccessIssue]
    if user is not None and user.passwordHash is not None:
        token = create_password_change_token(user.id)
        reset_link = f"{get_settings().frontend_url}/reset-password?token={token}"
        try:
            await send_password_reset_email(to=user.email, reset_link=reset_link)
        except Exception:
            # Never let an email-delivery failure change this endpoint's
            # response — that would leak account-existence information to
            # the caller. Delivery failures are operational, not the
            # caller's concern; log for ops visibility only.
            import logging

            logging.getLogger(__name__).warning(
                "forgot-password: failed to send reset email", exc_info=True
            )
    # Always 204, regardless of whether the email matched a real,
    # password-based account — no account-enumeration leak.
    return None


@router.post("/auth/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(
        limiter,
        route_key="auth_forgot_password",
        client_key=ip,
        config=per_minute_config(get_settings().rate_limit_forgot_password_per_minute),
    )
    try:
        claims = verify_password_change_token(payload.token)
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid or expired reset token: {exc}",
        ) from exc
    user_id = claims.sub
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None or getattr(user, "isActive", True) is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired reset token",
        )
    await db.user.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": user_id},
        data={
            "passwordHash": hash_password(payload.new_password),
            "mustChangePassword": False,
        },
    )
    return None


__all__ = [
    "AuthResponse",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "LoginRequest",
    "MustChangePasswordResponse",
    "RefreshRequest",
    "RegisterRequest",
    "ResetPasswordRequest",
    "TokenPairResponse",
    "router",
]
