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


__all__ = [
    "InvalidStateError",
    "McpTokenExpiredError",
    "McpTokenMissingError",
    "OAuthError",
    "TokenExchangeError",
    "TokenRefreshError",
    "build_authorize_url",
    "derive_resource",
    "generate_pkce_pair",
    "generate_state",
]
