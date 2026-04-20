"""MCP OAuth primitives.

All outbound OAuth calls (authorize URL, token exchange, token refresh,
client_credentials) include the RFC 8707 `resource` parameter (fix #1 from
CLAUDE.md §1). Tokens live server-side only, encrypted with
src/security/encryption.py (fix #4). get_valid_access_token falls back to
the server owner's token when a user accesses a shared server but has no
personal token (fix #5).

See Phase 3b spec §7, ADR-0011.
"""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from src.security.encryption import decrypt, encrypt


class OAuthError(RuntimeError):
    """Base class for all OAuth-layer errors."""


class InvalidStateError(OAuthError):
    """Callback state not found or expired."""


class TokenExchangeError(OAuthError):
    """IdP rejected the authorization code exchange."""


class TokenRefreshError(OAuthError):
    """IdP rejected the token refresh request."""


class McpTokenMissingError(OAuthError):
    """No token found for (server, user) and no fallback available."""


class McpTokenExpiredError(OAuthError):
    """Token expired with no refresh token available — user must re-authorize."""


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge_s256).

    Verifier: 64 random URL-safe bytes → 86-char string (within the 43-128
    range RFC 7636 permits). Challenge: SHA-256(verifier), base64url, no padding.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_state(nbytes: int = 32) -> str:
    """URL-safe random state for CSRF protection."""
    return secrets.token_urlsafe(nbytes)


def derive_resource(server_url: str) -> str:
    """RFC 8707: resource identifier is the target server's origin (scheme://host[:port]).

    Highspot rejects token exchange when `resource` differs from the MCP URL's
    origin. Unit tests assert this on every outbound OAuth call.
    """
    parts = urlsplit(server_url)
    return f"{parts.scheme}://{parts.netloc}"


_STATE_TTL = timedelta(minutes=5)


async def _reap_expired_state(db: Any) -> None:
    """Delete expired McpOAuthState rows. Called lazily on each authorize."""
    await db.mcpoauthstate.delete_many(where={"expiresAt": {"lt": datetime.now(UTC)}})


async def build_authorize_url(
    server: Any,
    user_id: str,
    redirect_uri: str,
    db: Any,
) -> str:
    """Return the OAuth authorize URL; insert McpOAuthState for the callback.

    The returned URL includes the RFC 8707 `resource` parameter (fix #1).
    """
    config = server.oauthConfig
    if not config:
        raise ValueError(
            f"MCP server {server.id!r} has no oauthConfig; cannot build authorize URL."
        )
    authorize_url = config.get("authorizeUrl")
    client_id = config.get("clientId")
    scopes = config.get("scopes") or []
    if not authorize_url or not client_id:
        raise ValueError(f"MCP server {server.id!r} oauthConfig missing authorizeUrl or clientId.")

    await _reap_expired_state(db)

    state = generate_state()
    verifier, challenge = generate_pkce_pair()
    now = datetime.now(UTC)
    expires_at = now + _STATE_TTL

    await db.mcpoauthstate.create(
        data={
            "mcpServerId": server.id,
            "userId": user_id,
            "state": state,
            "codeVerifier": verifier,
            "redirectUri": redirect_uri,
            "scope": " ".join(scopes) if scopes else None,
            "expiresAt": expires_at,
        }
    )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": " ".join(scopes) if scopes else "",
        "resource": derive_resource(server.url),  # fix #1
    }
    return f"{authorize_url}?{urlencode(params)}"


async def _validate_and_consume_state(state: str, db: Any) -> Any:
    """Find state row, check expiry, delete it (one-shot), return the row."""
    row = await db.mcpoauthstate.find_unique(where={"state": state})
    if row is None:
        raise InvalidStateError(f"OAuth state {state!r} not found (CSRF or replay).")
    if row.expiresAt < datetime.now(UTC):
        raise InvalidStateError(f"OAuth state {state!r} has expired.")
    await db.mcpoauthstate.delete(where={"state": state})
    return row


def _expires_at_from_in(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


async def exchange_code_for_tokens(
    server: Any,
    code: str,
    state: str,
    db: Any,
) -> Any:
    """Exchange authorization code for tokens; store encrypted in McpOAuthToken.

    Token-exchange POST includes RFC 8707 `resource` (fix #1).
    """
    state_row = await _validate_and_consume_state(state, db)

    config = server.oauthConfig or {}
    token_url = config["tokenUrl"]
    client_id = config["clientId"]
    client_secret = config.get("clientSecret", "")

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_row.redirectUri,
        "code_verifier": state_row.codeVerifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "resource": derive_resource(server.url),  # fix #1
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data=form,
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise TokenExchangeError(
            f"OAuth token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    payload = resp.json()
    access_token = payload.get("access_token", "")
    refresh_token: str | None = payload.get("refresh_token")
    expires_at = _expires_at_from_in(payload.get("expires_in"))

    encrypted_access = encrypt(access_token)
    encrypted_refresh = encrypt(refresh_token) if refresh_token else None

    token_data = {
        "encryptedAccessToken": encrypted_access,
        "encryptedRefreshToken": encrypted_refresh,
        "expiresAt": expires_at,
        "scope": payload.get("scope") or state_row.scope,
        "tokenType": payload.get("token_type", "Bearer"),
    }

    return await db.mcpoauthtoken.upsert(
        where={
            "mcpServerId_userId": {
                "mcpServerId": server.id,
                "userId": state_row.userId,
            }
        },
        data={
            "create": {
                "mcpServerId": server.id,
                "userId": state_row.userId,
                **token_data,
            },
            "update": token_data,
        },
    )


async def refresh_token(
    server: Any,
    token_row: Any,
    db: Any,
) -> Any:
    """Refresh an expired access token. POST includes RFC 8707 resource (fix #1).

    Updates token_row in place (via DB). Keeps the old refresh_token if the
    IdP doesn't return a new one.
    """
    if not token_row.encryptedRefreshToken:
        raise McpTokenExpiredError(
            f"Token for server {server.id!r} has no refresh token; user must re-authorize."
        )

    config = server.oauthConfig or {}
    token_url = config["tokenUrl"]
    client_id = config["clientId"]
    client_secret = config.get("clientSecret", "")
    refresh_plaintext = decrypt(token_row.encryptedRefreshToken)

    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_plaintext,
        "client_id": client_id,
        "client_secret": client_secret,
        "resource": derive_resource(server.url),  # fix #1
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data=form,
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise TokenRefreshError(
            f"OAuth refresh failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    payload = resp.json()
    access_token = payload.get("access_token", "")
    new_refresh: str | None = payload.get("refresh_token")
    expires_at = _expires_at_from_in(payload.get("expires_in"))

    update_data: dict[str, Any] = {
        "encryptedAccessToken": encrypt(access_token),
        "expiresAt": expires_at,
    }
    if new_refresh:
        update_data["encryptedRefreshToken"] = encrypt(new_refresh)
    else:
        # Keep the old encrypted refresh token (no rotation)
        update_data["encryptedRefreshToken"] = token_row.encryptedRefreshToken

    return await db.mcpoauthtoken.update(
        where={"id": token_row.id},
        data=update_data,
    )


__all__ = [
    "InvalidStateError",
    "McpTokenExpiredError",
    "McpTokenMissingError",
    "OAuthError",
    "TokenExchangeError",
    "TokenRefreshError",
    "build_authorize_url",
    "derive_resource",
    "exchange_code_for_tokens",
    "generate_pkce_pair",
    "generate_state",
    "refresh_token",
]
