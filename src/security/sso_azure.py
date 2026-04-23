"""Azure AD JWT verification (Phase 10a).

Fetches JWKS from the tenant's OIDC discovery endpoint, caches keys in
memory (24h TTL), validates signature + issuer + audience + expiry.

Public API: `verify_azure_jwt(token, tenant_id, expected_audience) -> claims`.
Raises AuthError on any failure.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from jose import JWTError
from jose import jwt as jose_jwt
from jose.exceptions import ExpiredSignatureError

from src.security.auth import AuthError

_JWKS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_JWKS_TTL_SECONDS = 24 * 60 * 60


async def _fetch_jwks(tenant_id: str) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _JWKS_CACHE.get(tenant_id)
    if cached is not None and (now - cached[0]) < _JWKS_TTL_SECONDS:
        return cached[1]

    discovery_url = (
        f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        disc = await client.get(discovery_url)
        disc.raise_for_status()
        jwks_uri = disc.json()["jwks_uri"]
        jwks_resp = await client.get(jwks_uri)
        jwks_resp.raise_for_status()
        keys = jwks_resp.json()["keys"]

    _JWKS_CACHE[tenant_id] = (now, keys)
    return keys


def _find_key(keys: list[dict[str, Any]], kid: str) -> dict[str, Any] | None:
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None


async def verify_azure_jwt(
    token: str,
    *,
    tenant_id: str,
    expected_audience: str,
) -> dict[str, Any]:
    """Validate an Azure-issued JWT against the tenant's JWKS.

    Returns the decoded claims dict.  Raises AuthError on any validation
    failure (signature, issuer, audience, expiry).
    """
    try:
        unverified_header = jose_jwt.get_unverified_header(token)
    except JWTError as exc:
        raise AuthError(f"invalid Azure JWT header: {exc}") from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise AuthError("Azure JWT missing kid header")

    keys = await _fetch_jwks(tenant_id)
    jwk = _find_key(keys, kid)
    if jwk is None:
        raise AuthError(f"unknown kid in Azure JWT: {kid}")

    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"

    try:
        claims: dict[str, Any] = jose_jwt.decode(
            token,
            jwk,  # python-jose accepts the raw JWK dict
            algorithms=["RS256"],
            audience=expected_audience,
            issuer=issuer,
        )
        return claims
    except ExpiredSignatureError as exc:
        raise AuthError(f"Azure JWT expired: {exc}") from exc
    except JWTError as exc:
        raise AuthError(f"Azure JWT validation failed: {exc}") from exc


__all__ = ["verify_azure_jwt"]
