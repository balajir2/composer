"""Unit tests for Azure AD JWT verification (Phase 10a).

Uses pytest-httpx to mock the OIDC discovery + JWKS endpoints.
For JWT signing in tests, use python-jose with an in-memory RSA key
generated on the fly.
"""

from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

from src.security.auth import AuthError
from src.security.sso_azure import (
    _JWKS_CACHE,  # pyright: ignore[reportPrivateUsage]
    verify_azure_jwt,
)


def _gen_rsa_key() -> tuple[Any, dict[str, Any]]:
    """Return (private_pem, public_jwk) for signing test tokens."""
    import base64

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_numbers = key.public_key().public_numbers()
    n = public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
    e = public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")
    jwk = {
        "kty": "RSA",
        "kid": "test-kid-1",
        "use": "sig",
        "alg": "RS256",
        "n": base64.urlsafe_b64encode(n).rstrip(b"=").decode(),
        "e": base64.urlsafe_b64encode(e).rstrip(b"=").decode(),
    }
    return private_pem, jwk


@pytest.fixture(autouse=True)
def _clear_jwks_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    _JWKS_CACHE.clear()


async def test_verify_happy_path(httpx_mock: Any) -> None:
    private_pem, jwk = _gen_rsa_key()
    tenant = "test-tenant"
    aud = "api://composer"
    discovery_url = (
        f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
    )
    httpx_mock.add_response(
        method="GET",
        url=discovery_url,
        json={"jwks_uri": "https://login.microsoftonline.com/test-tenant/discovery/keys"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://login.microsoftonline.com/test-tenant/discovery/keys",
        json={"keys": [jwk]},
    )

    token = jose_jwt.encode(
        {
            "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
            "aud": aud,
            "email": "alice@example.com",
            "name": "Alice",
            "exp": 9999999999,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-kid-1"},
    )

    claims = await verify_azure_jwt(token, tenant_id=tenant, expected_audience=aud)
    assert claims["email"] == "alice@example.com"


async def test_verify_rejects_wrong_audience(httpx_mock: Any) -> None:
    private_pem, jwk = _gen_rsa_key()
    tenant = "test-tenant"
    discovery_url = (
        f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
    )
    httpx_mock.add_response(
        method="GET",
        url=discovery_url,
        json={"jwks_uri": "https://login.microsoftonline.com/test-tenant/discovery/keys"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://login.microsoftonline.com/test-tenant/discovery/keys",
        json={"keys": [jwk]},
    )

    token = jose_jwt.encode(
        {
            "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
            "aud": "api://different",
            "email": "x@y.com",
            "exp": 9999999999,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-kid-1"},
    )

    with pytest.raises(AuthError, match=r"audience|aud|validation"):
        await verify_azure_jwt(token, tenant_id=tenant, expected_audience="api://composer")


async def test_verify_rejects_unknown_kid(httpx_mock: Any) -> None:
    _, jwk = _gen_rsa_key()
    tenant = "test-tenant"
    discovery_url = (
        f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
    )
    httpx_mock.add_response(
        method="GET",
        url=discovery_url,
        json={"jwks_uri": "https://login.microsoftonline.com/test-tenant/discovery/keys"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://login.microsoftonline.com/test-tenant/discovery/keys",
        json={"keys": [jwk]},
    )

    # Sign with a different private key → but we only expose the other kid.
    other_private_pem, _ = _gen_rsa_key()
    token = jose_jwt.encode(
        {"iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "aud": "x", "exp": 9999999999},
        other_private_pem,
        algorithm="RS256",
        headers={"kid": "unknown-kid"},
    )

    with pytest.raises(AuthError, match="unknown kid"):
        await verify_azure_jwt(token, tenant_id=tenant, expected_audience="x")
