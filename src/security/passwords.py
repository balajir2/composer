"""Bcrypt password hashing helpers.

bcrypt ships its own salt+rounds identification in the output hash
(`$2b$<rounds>$<salt><digest>` format); one column stores everything.

Used ONLY by standalone-mode /auth/register and /auth/login.
"""

import bcrypt  # pyright: ignore[reportMissingTypeStubs]

from src.config import get_settings


def hash_password(plaintext: str) -> str:
    """Hash a password with bcrypt using settings.bcrypt_rounds cost factor."""
    rounds = get_settings().bcrypt_rounds
    salted = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=rounds))
    return salted.decode("utf-8")


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """Constant-time comparison.  Malformed hash -> False (no exception).

    Returning False (rather than raising) keeps the timing side channel
    uniform — an attacker can't distinguish 'user doesn't exist' (empty
    hash stored) from 'wrong password' (valid hash mismatched).
    """
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


__all__ = ["hash_password", "verify_password"]
