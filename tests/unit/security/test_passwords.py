"""Tests for bcrypt password helpers (Phase 7a)."""

from src.security.passwords import hash_password, verify_password


def test_hash_and_verify_round_trip() -> None:
    h = hash_password("correct-horse-battery-staple")
    assert isinstance(h, str)
    # bcrypt hashes start with $2a$, $2b$, or $2y$
    assert h.startswith("$2")
    assert verify_password("correct-horse-battery-staple", h) is True


def test_verify_wrong_password() -> None:
    h = hash_password("password123")
    assert verify_password("password456", h) is False


def test_hash_is_randomized() -> None:
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2
    assert verify_password("same-password", h1) is True
    assert verify_password("same-password", h2) is True


def test_verify_malformed_hash_returns_false() -> None:
    # Invalid hash should not raise — return False so timing side channels
    # don't distinguish "user doesn't exist" from "wrong password"
    assert verify_password("any-pass", "not-a-bcrypt-hash") is False
    assert verify_password("any-pass", "") is False


def test_verify_empty_password_against_valid_hash() -> None:
    h = hash_password("real-password")
    assert verify_password("", h) is False
