"""AES-256-GCM encrypt/decrypt helpers.

Used by Phase 3a for MCP server static-auth tokens (api-key / bearer
values) and by Phase 3b for OAuth access/refresh tokens and client
secrets. Format: base64(nonce(12) || ciphertext || tag(16)).

Simpler than OAB's salt:iv:authTag:ciphertext scheme — no salt because
we don't derive the key (we use the raw 32-byte key from settings
directly). This is a deliberate simplification; documented here.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config import get_settings


class EncryptionError(RuntimeError):
    """Raised when encryption or decryption fails for any reason."""


class EncryptionKeyMissingError(EncryptionError):
    """Raised when ENCRYPTION_KEY is empty or not configured."""


_NONCE_BYTES = 12


def _load_key() -> bytes:
    raw = get_settings().encryption_key
    if not raw:
        raise EncryptionKeyMissingError(
            "ENCRYPTION_KEY is not configured. Generate with: "
            'python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"'
        )
    try:
        key = base64.b64decode(raw)
    except (ValueError, binascii.Error) as exc:
        raise EncryptionError(f"ENCRYPTION_KEY is not valid base64: {exc}") from exc
    if len(key) != 32:
        raise EncryptionError(f"ENCRYPTION_KEY must decode to 32 bytes, got {len(key)}")
    return key


def encrypt(plaintext: str) -> str:
    """Encrypt a string, return base64(nonce || ciphertext || tag)."""
    key = _load_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_BYTES)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)  # associated_data = None
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(ciphertext_b64: str) -> str:
    """Reverse of encrypt(). Raises EncryptionError on tamper, bad base64, bad key."""
    key = _load_key()
    try:
        raw = base64.b64decode(ciphertext_b64.encode("ascii"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EncryptionError(f"Not valid base64: {exc}") from exc
    if len(raw) < _NONCE_BYTES + 16:  # nonce + tag minimum
        raise EncryptionError("Ciphertext is too short to contain nonce + tag")
    nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    try:
        pt = AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as exc:
        raise EncryptionError("Decryption failed: authentication tag mismatch") from exc
    return pt.decode("utf-8")


# Generic marker-prefixed encryption for secret-bearing fields stored inline
# in JSON columns (workflow node data, MCP server headers/oauth config) —
# same wire format Jira's apiToken already uses (JIRA_TOKEN_ENC_PREFIX in
# src/engine/workflow.py), extracted here so P0-5's vector-DB/HTTP/MCP
# fields don't each reimplement it. Jira's own helpers are left as-is
# (already shipped and tested) but are wire-compatible with these.
SECRET_ENC_PREFIX = "enc:v1:"
REDACTED_MARKER = "••••••••"


def is_marked_encrypted(value: str) -> bool:
    return value.startswith(SECRET_ENC_PREFIX)


def encrypt_marked(plaintext: str) -> str:
    """Encrypt a string for storage in a JSON column, self-identifying via prefix."""
    return SECRET_ENC_PREFIX + encrypt(plaintext)


def decrypt_marked(value: str) -> str:
    """Reverse of encrypt_marked(). Values without the prefix pass through
    unchanged, so fields saved before encryption was added keep working
    with no backfill migration required."""
    if not is_marked_encrypted(value):
        return value
    return decrypt(value[len(SECRET_ENC_PREFIX) :])


# Header NAMES (case-insensitive) whose VALUES get the same encrypt/redact
# treatment as a single secret field — used for the http and mcp node's
# arbitrary headers dicts, where most entries (Content-Type, Accept, ...)
# are not secret but a few commonly are. Mirrors the query-param list
# already used for URL redaction (src/executors/http.py's
# _SENSITIVE_QUERY_PARAMS, P0-6).
SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-access-token",
        "access-token",
    }
)


def is_sensitive_header_name(name: str) -> bool:
    return name.lower() in SENSITIVE_HEADER_NAMES


def redact_sensitive_headers(headers: dict[str, str]) -> dict[str, str]:
    """Mask secret-looking header VALUES for return in an API response.
    Header names stay visible (e.g. 'Content-Type') — only values for
    names in SENSITIVE_HEADER_NAMES get replaced with REDACTED_MARKER."""
    return {
        k: (REDACTED_MARKER if is_sensitive_header_name(k) and v else v) for k, v in headers.items()
    }


def encrypt_sensitive_headers(
    headers: dict[str, str], prior_headers: dict[str, str] | None = None
) -> dict[str, str]:
    """Encrypt secret-looking header VALUES before persisting.

    If an incoming value is the redacted marker (the UI echoed back what a
    prior read returned, unchanged), preserve whatever was previously
    stored under that header name instead of persisting the literal
    marker string — same preserve-on-no-op-save guarantee as
    encrypt_jira_api_token's callers.
    """
    prior = prior_headers or {}
    result: dict[str, str] = {}
    for k, v in headers.items():
        if not is_sensitive_header_name(k) or not v:
            result[k] = v
            continue
        if v == REDACTED_MARKER:
            result[k] = prior.get(k, v)
        elif not is_marked_encrypted(v):
            result[k] = encrypt_marked(v)
        else:
            result[k] = v
    return result


def decrypt_sensitive_headers(headers: dict[str, str]) -> dict[str, str]:
    """Decrypt secret-looking header VALUES immediately before actual
    outbound use. Values without the marker prefix pass through
    unchanged (decrypt_marked's guarantee), so this is also safe to call
    on headers saved before encryption existed."""
    return {
        k: (decrypt_marked(v) if is_sensitive_header_name(k) else v) for k, v in headers.items()
    }


__all__ = [
    "REDACTED_MARKER",
    "SECRET_ENC_PREFIX",
    "SENSITIVE_HEADER_NAMES",
    "EncryptionError",
    "EncryptionKeyMissingError",
    "decrypt",
    "decrypt_marked",
    "decrypt_sensitive_headers",
    "encrypt",
    "encrypt_marked",
    "encrypt_sensitive_headers",
    "is_marked_encrypted",
    "is_sensitive_header_name",
    "redact_sensitive_headers",
]
