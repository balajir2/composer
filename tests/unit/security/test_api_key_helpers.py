"""Unit tests for API key generation + hashing."""

from src.security.api_keys import (
    extract_prefix,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)


def test_generate_api_key_has_expected_shape() -> None:
    key = generate_api_key()
    assert key.startswith("ck_")
    assert len(key) == 35  # 'ck_' (3) + 32 random
    assert all(c.isalnum() or c == "_" for c in key)


def test_extract_prefix_is_first_12_chars() -> None:
    key = "ck_abcdefghijklmnop"
    assert extract_prefix(key) == "ck_abcdefghi"


def test_hash_and_verify_roundtrip() -> None:
    key = generate_api_key()
    hashed = hash_api_key(key, rounds=4)  # low cost for test speed
    assert verify_api_key(key, hashed) is True
    assert verify_api_key("ck_wrong", hashed) is False


def test_verify_rejects_malformed_hash() -> None:
    # bcrypt raises ValueError on invalid hash; helper should catch.
    assert verify_api_key("ck_anything", "not-a-valid-bcrypt-hash") is False


def test_generate_produces_unique_keys() -> None:
    # Collision probability negligible across a handful of calls.
    keys = {generate_api_key() for _ in range(50)}
    assert len(keys) == 50
