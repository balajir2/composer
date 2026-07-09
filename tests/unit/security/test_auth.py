"""Tests for the auth middleware primitives (Phase 7a, ADR-0014 + 0015).

Phase 1's jwt module uses python-jose and `get_settings()` internally.
`create_access_token(user_id)` is the token factory (no settings param).

For embedded-mode tests that need custom claims (iss), we encode JWTs
directly via jose.jwt.encode to avoid being constrained by Phase 1's helper.
"""

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt

from src.security.auth import (
    AuthError,
    _ensure_active_user,  # pyright: ignore[reportPrivateUsage]
    _extract_bearer,  # pyright: ignore[reportPrivateUsage]
    _verify_embedded_jwt,  # pyright: ignore[reportPrivateUsage]
    _verify_standalone_jwt,  # pyright: ignore[reportPrivateUsage]
    get_current_user_id,
)

# ─── Mock helpers ────────────────────────────────────────────────────────────


def _mock_request(auth_header: str | None = None) -> Any:
    req = MagicMock()

    def _get(key: str, default: str = "") -> str:
        if key.lower() == "authorization":
            return auth_header if auth_header is not None else default
        return default

    req.headers.get = _get
    return req


def _mock_settings(
    *,
    deployment_mode: str = "standalone",
    environment: str = "production",
    jwt_secret: str = "test-secret-at-least-32-chars-long-for-hs256",
    jwt_algorithm: str = "HS256",
    iep_shared_secret: str = "",
    iep_jwt_issuer: str = "",
    iep_jwks_url: str = "",
    jwt_access_ttl_seconds: int = 3600,
    jwt_refresh_ttl_seconds: int = 604800,
) -> Any:
    s = MagicMock()
    s.deployment_mode = deployment_mode
    s.environment = environment
    s.jwt_secret = jwt_secret
    s.jwt_algorithm = jwt_algorithm
    s.iep_shared_secret = iep_shared_secret
    s.iep_jwt_issuer = iep_jwt_issuer
    s.iep_jwks_url = iep_jwks_url
    s.jwt_access_ttl_seconds = jwt_access_ttl_seconds
    s.jwt_refresh_ttl_seconds = jwt_refresh_ttl_seconds
    return s


def _make_jose_token(
    *,
    sub: str,
    secret: str,
    algorithm: str = "HS256",
    iss: str | None = None,
    token_type: str | None = "access",
    exp_offset: int = 3600,
) -> str:
    """Encode a JWT directly with python-jose for test control."""
    now = int(time.time())
    payload: dict[str, Any] = {"sub": sub, "iat": now, "exp": now + exp_offset}
    if iss is not None:
        payload["iss"] = iss
    if token_type is not None:
        payload["type"] = token_type
    return jose_jwt.encode(payload, secret, algorithm=algorithm)


# ─── _extract_bearer ─────────────────────────────────────────────────────────


def test_extract_bearer_happy() -> None:
    req = _mock_request("Bearer abc.def.ghi")
    assert _extract_bearer(req) == "abc.def.ghi"


def test_extract_bearer_missing() -> None:
    req = _mock_request(None)
    assert _extract_bearer(req) is None


def test_extract_bearer_empty() -> None:
    req = _mock_request("")
    assert _extract_bearer(req) is None


def test_extract_bearer_wrong_scheme() -> None:
    req = _mock_request("Basic dXNlcjpwYXNz")
    assert _extract_bearer(req) is None


def test_extract_bearer_case_insensitive_scheme() -> None:
    req = _mock_request("bearer token.value.here")
    assert _extract_bearer(req) == "token.value.here"


# ─── _verify_standalone_jwt ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_standalone_jwt_happy() -> None:
    """Token created with the Composer secret round-trips correctly."""
    secret = "test-secret-at-least-32-chars-long-for-hs256"
    settings = _mock_settings(jwt_secret=secret)
    token = _make_jose_token(sub="user-1", secret=secret)
    user_id = await _verify_standalone_jwt(token, settings)
    assert user_id == "user-1"


@pytest.mark.asyncio
async def test_verify_standalone_jwt_bad_token() -> None:
    settings = _mock_settings()
    with pytest.raises(AuthError):
        await _verify_standalone_jwt("not.a.jwt", settings)


@pytest.mark.asyncio
async def test_verify_standalone_jwt_wrong_secret() -> None:
    """Token signed with a different secret must be rejected."""
    token = _make_jose_token(sub="user-x", secret="wrong-secret-padded-to-32chars-xx")
    settings = _mock_settings(jwt_secret="right-secret-padded-to-32chars-xx")
    with pytest.raises(AuthError):
        await _verify_standalone_jwt(token, settings)


@pytest.mark.asyncio
async def test_verify_standalone_jwt_via_phase1_helper() -> None:
    """Tokens issued by Phase 1's create_access_token verify through _verify_standalone_jwt.

    create_access_token reads get_settings() internally, so we patch it to
    use the same secret the verifier sees.
    """
    from src.config import Settings
    from src.security.jwt import create_access_token

    secret = "test-secret-at-least-32-chars-long-for-hs256"
    fake_settings = Settings(
        jwt_secret=secret,
        database_url="postgresql://x:x@localhost/x",
    )
    with patch("src.security.jwt.get_settings", return_value=fake_settings):
        token = create_access_token("phase1-user")

    settings = _mock_settings(jwt_secret=secret)
    user_id = await _verify_standalone_jwt(token, settings)
    assert user_id == "phase1-user"


@pytest.mark.asyncio
async def test_verify_standalone_jwt_rejects_refresh_token() -> None:
    """A 30-day refresh token must never authenticate an API request."""
    secret = "test-secret-at-least-32-chars-long-for-hs256"
    settings = _mock_settings(jwt_secret=secret)
    token = _make_jose_token(sub="user-1", secret=secret, token_type="refresh")
    with pytest.raises(AuthError, match="access token"):
        await _verify_standalone_jwt(token, settings)


@pytest.mark.asyncio
async def test_ensure_active_user_rejects_deactivated_account() -> None:
    db = MagicMock()
    db.user.find_unique = AsyncMock(return_value=MagicMock(isActive=False))
    with pytest.raises(AuthError, match="deactivated"):
        await _ensure_active_user(db, "user-1")


# ─── _verify_embedded_jwt ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_embedded_jwt_no_config_raises() -> None:
    settings = _mock_settings(deployment_mode="embedded", iep_shared_secret="")
    with pytest.raises(AuthError, match="no IEP JWT verification configured"):
        await _verify_embedded_jwt("anything", settings)


@pytest.mark.asyncio
async def test_verify_embedded_jwt_happy() -> None:
    """Valid IEP token with correct iss verifies successfully."""
    iep_secret = "iep-shared-secret-padded-to-32chars"
    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret=iep_secret,
        iep_jwt_issuer="https://iep.test",
    )
    token = _make_jose_token(sub="u2", secret=iep_secret, iss="https://iep.test")
    user_id = await _verify_embedded_jwt(token, settings)
    assert user_id == "u2"


@pytest.mark.asyncio
async def test_verify_embedded_jwt_wrong_issuer() -> None:
    """Token with wrong iss claim must be rejected."""
    iep_secret = "iep-shared-secret-padded-to-32chars"
    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret=iep_secret,
        iep_jwt_issuer="https://iep.test",
    )
    token = _make_jose_token(sub="u3", secret=iep_secret, iss="https://attacker.example")
    with pytest.raises(AuthError, match="issuer mismatch"):
        await _verify_embedded_jwt(token, settings)


@pytest.mark.asyncio
async def test_verify_embedded_jwt_no_issuer_check_when_unconfigured() -> None:
    """When iep_jwt_issuer is empty, any iss (or none) is accepted."""
    iep_secret = "iep-shared-secret-padded-to-32chars"
    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret=iep_secret,
        iep_jwt_issuer="",  # no issuer configured → skip check
    )
    token = _make_jose_token(sub="u4", secret=iep_secret, iss="https://anything.example")
    user_id = await _verify_embedded_jwt(token, settings)
    assert user_id == "u4"


@pytest.mark.asyncio
async def test_verify_embedded_jwt_bad_secret() -> None:
    token = _make_jose_token(sub="u5", secret="wrong-secret-padded-to-32chars-xx")
    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret="right-secret-padded-to-32chars-xx",
    )
    with pytest.raises(AuthError):
        await _verify_embedded_jwt(token, settings)


# ─── get_current_user_id ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_current_user_id_dev_fallback() -> None:
    """ADR-0015: no Authorization header + environment=development → 'dev'."""
    req = _mock_request(None)
    settings = _mock_settings(environment="development")
    user_id = await get_current_user_id(req, settings)
    assert user_id == "dev"


@pytest.mark.asyncio
async def test_get_current_user_id_production_no_header_raises() -> None:
    """Production requires Authorization header; missing → 401."""
    req = _mock_request(None)
    settings = _mock_settings(environment="production")
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(req, settings)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_id_standalone_happy() -> None:
    secret = "test-secret-at-least-32-chars-long-for-hs256"
    settings = _mock_settings(deployment_mode="standalone", jwt_secret=secret)
    token = _make_jose_token(sub="u1", secret=secret)
    req = _mock_request(f"Bearer {token}")
    assert await get_current_user_id(req, settings) == "u1"


@pytest.mark.asyncio
async def test_get_current_user_id_routes_to_embedded_verifier() -> None:
    """deployment_mode=embedded routes to _verify_embedded_jwt."""
    iep_secret = "iep-shared-secret-padded-to-32chars"
    settings = _mock_settings(
        deployment_mode="embedded",
        iep_shared_secret=iep_secret,
        iep_jwt_issuer="",
        environment="production",
    )
    token = _make_jose_token(sub="embedded-user", secret=iep_secret)
    req = _mock_request(f"Bearer {token}")
    assert await get_current_user_id(req, settings) == "embedded-user"


@pytest.mark.asyncio
async def test_get_current_user_id_invalid_token_raises_401() -> None:
    """Bad token in standalone mode → 401, not 500."""
    settings = _mock_settings(deployment_mode="standalone")
    req = _mock_request("Bearer not.valid.jwt")
    with pytest.raises(HTTPException) as excinfo:
        await get_current_user_id(req, settings)
    assert excinfo.value.status_code == 401


def test_get_user_id_allow_password_change_accepts_access_token() -> None:
    import asyncio

    from src.security.auth import get_user_id_allow_password_change
    from src.security.jwt import create_access_token

    token = create_access_token("u1")
    req = _mock_request(f"Bearer {token}")
    user_id = asyncio.get_event_loop().run_until_complete(
        get_user_id_allow_password_change(req)
    )
    assert user_id == "u1"


def test_get_user_id_allow_password_change_accepts_password_change_token() -> None:
    import asyncio

    from src.security.auth import get_user_id_allow_password_change
    from src.security.jwt import create_password_change_token

    token = create_password_change_token("u1")
    req = _mock_request(f"Bearer {token}")
    user_id = asyncio.get_event_loop().run_until_complete(
        get_user_id_allow_password_change(req)
    )
    assert user_id == "u1"


def test_get_user_id_allow_password_change_rejects_refresh_token() -> None:
    import asyncio

    from src.security.auth import AuthError, get_user_id_allow_password_change
    from src.security.jwt import create_refresh_token

    token = create_refresh_token("u1")
    req = _mock_request(f"Bearer {token}")
    with pytest.raises(AuthError):
        asyncio.get_event_loop().run_until_complete(
            get_user_id_allow_password_change(req)
        )


def test_get_user_id_allow_password_change_rejects_missing_header() -> None:
    import asyncio

    from src.security.auth import AuthError, get_user_id_allow_password_change

    req = _mock_request(None)
    with pytest.raises(AuthError):
        asyncio.get_event_loop().run_until_complete(
            get_user_id_allow_password_change(req)
        )
