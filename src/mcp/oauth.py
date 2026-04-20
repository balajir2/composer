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
from urllib.parse import urlsplit


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


__all__ = [
    "InvalidStateError",
    "McpTokenExpiredError",
    "McpTokenMissingError",
    "OAuthError",
    "TokenExchangeError",
    "TokenRefreshError",
    "derive_resource",
    "generate_pkce_pair",
    "generate_state",
]
