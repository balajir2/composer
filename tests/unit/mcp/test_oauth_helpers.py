"""Tests for PKCE, state, and resource-derivation helpers."""

import base64
import hashlib

from src.mcp.oauth import derive_resource, generate_pkce_pair, generate_state


def test_pkce_verifier_length_and_charset() -> None:
    verifier, _challenge = generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    # URL-safe base64 alphabet (no padding)
    assert all(c.isalnum() or c in "-_" for c in verifier)
    assert "=" not in verifier


def test_pkce_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_pkce_pairs_are_unique() -> None:
    pairs = {generate_pkce_pair() for _ in range(10)}
    assert len(pairs) == 10


def test_state_is_url_safe() -> None:
    s = generate_state()
    assert all(c.isalnum() or c in "-_" for c in s)
    assert len(s) >= 20


def test_state_values_are_unique() -> None:
    states = {generate_state() for _ in range(10)}
    assert len(states) == 10


def test_derive_resource_strips_path_and_query() -> None:
    assert derive_resource("https://api.highspot.com/mcp") == "https://api.highspot.com"
    assert derive_resource("https://api.highspot.com/mcp?x=1") == "https://api.highspot.com"
    assert derive_resource("https://api.highspot.com/a/b/c") == "https://api.highspot.com"


def test_derive_resource_preserves_port() -> None:
    assert derive_resource("https://example.com:8443/mcp") == "https://example.com:8443"


def test_derive_resource_preserves_scheme() -> None:
    assert derive_resource("http://localhost:9000/mcp") == "http://localhost:9000"
