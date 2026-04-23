"""API key generation, hashing, prefix extraction.

Format: 'ck_<32-random-alphanumeric>' = 35 chars total.
Prefix stored unhashed (first 12 chars: 'ck_abc12345') for display + O(1)
lookup; full key is bcrypt-hashed (same cost factor as user passwords).
"""

from __future__ import annotations

import secrets

import bcrypt

_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_KEY_PREFIX_SCHEME = "ck_"
_KEY_RANDOM_LEN = 32
_KEY_DISPLAY_PREFIX_LEN = 12  # "ck_" + first 9 random chars


def generate_api_key() -> str:
    """Return a fresh API key string, e.g. 'ck_a2B7x...'."""
    tail = "".join(secrets.choice(_ALPHABET) for _ in range(_KEY_RANDOM_LEN))
    return f"{_KEY_PREFIX_SCHEME}{tail}"


def extract_prefix(key: str) -> str:
    """Return the first 12 chars ('ck_' + 9 random) used for display + DB lookup."""
    return key[:_KEY_DISPLAY_PREFIX_LEN]


def hash_api_key(key: str, *, rounds: int = 12) -> str:
    """bcrypt-hash the full key for storage."""
    return bcrypt.hashpw(key.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def verify_api_key(key: str, hashed: str) -> bool:
    """Constant-time comparison of key against stored bcrypt hash."""
    try:
        return bcrypt.checkpw(key.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


__all__ = [
    "extract_prefix",
    "generate_api_key",
    "hash_api_key",
    "verify_api_key",
]
