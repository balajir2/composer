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


__all__ = ["EncryptionError", "EncryptionKeyMissingError", "decrypt", "encrypt"]
