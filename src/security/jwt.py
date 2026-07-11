"""JWT primitives.

Phase 1 ships these as library code only — no FastAPI dependency is wired
to routes (per ADR-0005). Phase 7 assembles them into middleware.

HS256 via python-jose. Token shape mirrors IE's DES-004 pattern: a simple
subject + type + iat/exp set.
"""

import time

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel, ValidationError

from src.config import get_settings


class AccessTokenPayload(BaseModel):
    sub: str
    iat: int
    exp: int
    type: str = "access"


class RefreshTokenPayload(BaseModel):
    sub: str
    iat: int
    exp: int
    type: str = "refresh"


class PasswordChangeTokenPayload(BaseModel):
    sub: str
    iat: int
    exp: int
    type: str = "password_change"


class ApprovalEmailTokenPayload(BaseModel):
    sub: str  # execution_id
    node_id: str
    decision: str  # "approved" | "rejected"
    approver_email: str
    # Binds the token to the specific pause *instance*, not just the node --
    # a `while`-loop user-approval node can pause repeatedly at the same
    # node_id, and without this a stale token from an earlier iteration
    # would remain valid to resolve a later one. Mirrors
    # `_pending_approval_since`, stamped fresh on every pause by
    # LangGraphExecutor._mark_waiting_approval.
    pending_since: str
    iat: int
    exp: int
    type: str = "approval_email"


class TokenVerificationError(ValueError):
    """Raised when a JWT cannot be verified (signature, shape, or type)."""


class TokenExpiredError(TokenVerificationError):
    """Raised specifically when a token is well-formed but past its `exp`."""


def _now() -> int:
    """Indirection for tests to freeze time."""
    return int(time.time())


def _encode(payload: BaseModel) -> str:
    settings = get_settings()
    return jwt.encode(
        payload.model_dump(),
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _decode(token: str) -> dict[str, object]:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except ExpiredSignatureError as exc:
        raise TokenExpiredError(f"Token has expired: {exc}") from exc
    except JWTError as exc:
        raise TokenVerificationError(f"Invalid token: {exc}") from exc


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    now = _now()
    payload = AccessTokenPayload(
        sub=user_id,
        iat=now,
        exp=now + settings.jwt_access_ttl_seconds,
    )
    return _encode(payload)


def create_refresh_token(user_id: str) -> str:
    settings = get_settings()
    now = _now()
    payload = RefreshTokenPayload(
        sub=user_id,
        iat=now,
        exp=now + settings.jwt_refresh_ttl_seconds,
    )
    return _encode(payload)


def create_password_change_token(user_id: str) -> str:
    settings = get_settings()
    now = _now()
    payload = PasswordChangeTokenPayload(
        sub=user_id,
        iat=now,
        exp=now + settings.jwt_password_change_ttl_seconds,
    )
    return _encode(payload)


def create_approval_email_token(
    execution_id: str, node_id: str, decision: str, approver_email: str, pending_since: str
) -> str:
    settings = get_settings()
    now = _now()
    payload = ApprovalEmailTokenPayload(
        sub=execution_id,
        node_id=node_id,
        decision=decision,
        approver_email=approver_email,
        pending_since=pending_since,
        iat=now,
        exp=now + settings.approval_link_ttl_hours * 3600,
    )
    return _encode(payload)


def verify_access_token(token: str) -> AccessTokenPayload:
    raw = _decode(token)
    if raw.get("type") != "access":
        raise TokenVerificationError(f"Expected token type 'access', got {raw.get('type')!r}")
    try:
        return AccessTokenPayload.model_validate(raw)
    except ValidationError as exc:
        raise TokenVerificationError(f"Malformed access token payload: {exc}") from exc


def verify_refresh_token(token: str) -> RefreshTokenPayload:
    raw = _decode(token)
    if raw.get("type") != "refresh":
        raise TokenVerificationError(f"Expected token type 'refresh', got {raw.get('type')!r}")
    try:
        return RefreshTokenPayload.model_validate(raw)
    except ValidationError as exc:
        raise TokenVerificationError(f"Malformed refresh token payload: {exc}") from exc


def verify_password_change_token(token: str) -> PasswordChangeTokenPayload:
    raw = _decode(token)
    if raw.get("type") != "password_change":
        raise TokenVerificationError(
            f"Expected token type 'password_change', got {raw.get('type')!r}"
        )
    try:
        return PasswordChangeTokenPayload.model_validate(raw)
    except ValidationError as exc:
        raise TokenVerificationError(f"Malformed password_change token payload: {exc}") from exc


def verify_approval_email_token(token: str) -> ApprovalEmailTokenPayload:
    raw = _decode(token)
    if raw.get("type") != "approval_email":
        raise TokenVerificationError(
            f"Expected token type 'approval_email', got {raw.get('type')!r}"
        )
    try:
        return ApprovalEmailTokenPayload.model_validate(raw)
    except ValidationError as exc:
        raise TokenVerificationError(f"Malformed approval_email token payload: {exc}") from exc


__all__ = [
    "AccessTokenPayload",
    "ApprovalEmailTokenPayload",
    "PasswordChangeTokenPayload",
    "RefreshTokenPayload",
    "TokenExpiredError",
    "TokenVerificationError",
    "create_access_token",
    "create_approval_email_token",
    "create_password_change_token",
    "create_refresh_token",
    "verify_access_token",
    "verify_approval_email_token",
    "verify_password_change_token",
    "verify_refresh_token",
]
