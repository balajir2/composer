"""Auth middleware primitives for Phase 7a.

`get_current_user_id` is the FastAPI dependency every user-scoped route
uses.  It dispatches between standalone (HS256 with Composer's JWT_SECRET)
and embedded (IEP-signed JWT) verification based on `settings.deployment_mode`.

Dev-mode fallback (ADR-0015): when `environment=development` AND no
Authorization header is present, return user_id='dev' to keep existing
integration tests working.  Unreachable when `environment=production`.

Implementation note: Phase 1's jwt.py (_decode) reads settings via
get_settings() internally and only decodes with Composer's own secret.
For auth.py we need to decode with arbitrary secrets (IEP's shared secret,
or to avoid coupling to Phase 1's internal wiring), so we call
jose.jwt.decode directly — the same library Phase 1 uses.
"""

import contextlib
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from jose import jwt as jose_jwt
from jose.exceptions import ExpiredSignatureError

from src.config import Settings, get_settings
from src.security.jwt import (
    TokenVerificationError,
    verify_access_token,
    verify_password_change_token,
)


class AuthError(HTTPException):
    """401 Unauthorized with a plain-text detail string.

    Raised by all auth primitives.  FastAPI returns this as HTTP 401.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _extract_bearer(request: Request) -> str | None:
    """Return the raw JWT from 'Authorization: Bearer <token>', or None.

    Scheme comparison is case-insensitive; empty token after stripping → None.
    """
    header: str = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[len("bearer ") :].strip()
    return token or None


def _jose_decode(token: str, secret: str, algorithms: list[str] | None = None) -> dict[str, Any]:
    """Decode and validate a JWT using python-jose.

    Raises AuthError on any failure (expired, bad signature, malformed).
    """
    algs = algorithms or ["HS256"]
    try:
        result: dict[str, Any] = jose_jwt.decode(token, secret, algorithms=algs)
        return result
    except ExpiredSignatureError as exc:
        raise AuthError(f"JWT has expired: {exc}") from exc
    except JWTError as exc:
        raise AuthError(f"invalid JWT: {exc}") from exc
    except Exception as exc:
        raise AuthError(f"JWT decode error: {type(exc).__name__}: {exc}") from exc


async def _verify_standalone_jwt(token: str, settings: Any) -> str:
    """Verify an HS256 JWT signed with Composer's own JWT_SECRET.

    Returns the 'sub' claim (user_id).  Raises AuthError on any failure.
    """
    claims = _jose_decode(token, settings.jwt_secret)
    if claims.get("type") != "access":
        raise AuthError("JWT must be an access token")
    sub = claims.get("sub")
    if not sub:
        raise AuthError("JWT missing 'sub' claim")
    return str(sub)


async def _ensure_active_user(db: Any, user_id: str) -> None:
    """Reject credentials for a deactivated or deleted standalone user."""
    user = await db.user.find_unique(where={"id": user_id})
    if user is None:
        raise AuthError("user no longer exists")
    if getattr(user, "isActive", True) is False:
        raise AuthError("account is deactivated")


async def verify_user_token(token: str, settings: Any) -> str:
    """Verify a bearer JWT using the configured deployment-mode trust model."""
    if settings.deployment_mode == "embedded":
        return await _verify_embedded_jwt(token, settings)
    return await _verify_standalone_jwt(token, settings)


async def _verify_embedded_jwt(token: str, settings: Any) -> str:
    """Verify an IEP-signed JWT.

    Phase 7a: HS256 shared-secret path only.
    Phase 7b (future): adds JWKS/RS256 path via settings.iep_jwks_url.

    Raises AuthError when:
    - iep_shared_secret is not configured
    - the token fails signature/expiry verification
    - the 'iss' claim doesn't match settings.iep_jwt_issuer (when set)
    - the 'sub' claim is absent
    """
    if not settings.iep_shared_secret:
        raise AuthError(
            "embedded mode: no IEP JWT verification configured "
            "(set IEP_JWKS_URL or IEP_SHARED_SECRET)"
        )

    claims = _jose_decode(token, settings.iep_shared_secret)

    # Issuer check — only enforced when iep_jwt_issuer is configured.
    if settings.iep_jwt_issuer and claims.get("iss") != settings.iep_jwt_issuer:
        raise AuthError("IEP JWT issuer mismatch")

    sub = claims.get("sub")
    if not sub:
        raise AuthError("JWT missing 'sub' claim")

    return str(sub)


async def get_current_user_id(
    request: Request,
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> str:
    """FastAPI dependency — returns the authenticated user_id.

    Routing logic:
    1. Extract Bearer token from Authorization header.
    2. If no token AND environment=development → return 'dev' (ADR-0015 fallback).
    3. If no token AND environment=production → raise 401.
    4. If deployment_mode=embedded → _verify_embedded_jwt.
    5. Otherwise (standalone) → _verify_standalone_jwt.

    All failure paths raise AuthError (HTTP 401).  No other exception escapes.
    """
    token = _extract_bearer(request)

    if token is None:
        if settings.environment == "development":
            return "dev"
        raise AuthError("missing Authorization header")

    user_id = await verify_user_token(token, settings)
    if settings.deployment_mode == "embedded":
        return user_id

    # Standalone tokens remain cryptographically valid after an account is
    # deactivated. Check the live user row when the app-wide Prisma client is
    # available so deactivation takes effect immediately on every JWT route.
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

    db = getattr(request.app.state, "db", None)
    if isinstance(db, Prisma):
        await _ensure_active_user(db, user_id)
    return user_id


async def get_user_id_allow_password_change(request: Request) -> str:
    """Standalone-mode only. Accepts EITHER a normal access token (self-service
    change while already logged in) OR a password_change token (completing an
    admin-forced reset). Raises AuthError (401) for anything else — including
    refresh tokens, which must never authorize this endpoint.

    Like `get_current_user_id`, this also checks the live user row when the
    app-wide Prisma client is available, so a deactivated account can't
    authenticate here even with a still-valid token (see the "Standalone
    tokens remain cryptographically valid..." comment above).
    """
    token = _extract_bearer(request)
    if token is None:
        raise AuthError("missing Authorization header")

    user_id: str | None = None
    with contextlib.suppress(TokenVerificationError):
        user_id = verify_access_token(token).sub
    if user_id is None:
        try:
            user_id = verify_password_change_token(token).sub
        except TokenVerificationError as exc:
            raise AuthError(f"invalid token: {exc}") from exc

    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

    db = getattr(request.app.state, "db", None)
    if isinstance(db, Prisma):
        await _ensure_active_user(db, user_id)
    return user_id


async def get_current_role(
    request: Request,
    settings: Settings = Depends(get_settings),  # pyright: ignore[reportCallIssue]
) -> tuple[str, str]:
    """Returns (user_id, role).  role is 'admin' or 'member'.

    Fetches the User row to read role.  Dev-mode fallback ('dev' user_id)
    returns 'member' — dev-mode is never admin unless the 'dev' User row
    is explicitly seeded with role=admin in the DB (which Composer's
    standalone auth does NOT do automatically).
    """
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

    user_id = await get_current_user_id(request, settings)

    # Fetch role from DB — cheap single-row lookup keyed on primary id.
    db = getattr(request.app.state, "db", None)
    if db is None or not isinstance(db, Prisma):
        return user_id, "member"
    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    role = getattr(user, "role", None)
    role_str = (
        str(role.value) if role is not None and hasattr(role, "value") else str(role or "member")
    )
    return user_id, role_str


async def ensure_admin(
    user_and_role: tuple[str, str] = Depends(get_current_role),
) -> str:
    """Admin-only gate.  Returns caller user_id.  Raises 403 if not admin."""
    user_id, role = user_and_role
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return user_id


__all__ = [
    "AuthError",
    "ensure_admin",
    "get_current_role",
    "get_current_user_id",
    "get_user_id_allow_password_change",
    "verify_user_token",
]
