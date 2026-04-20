"""Tests for JWT primitives."""

import pytest

from src.security.jwt import (
    TokenExpiredError,
    TokenVerificationError,
    create_access_token,
    create_refresh_token,
    verify_access_token,
    verify_refresh_token,
)


def test_access_token_round_trip() -> None:
    token = create_access_token("user-123")
    payload = verify_access_token(token)
    assert payload.sub == "user-123"
    assert payload.type == "access"


def test_refresh_token_round_trip() -> None:
    token = create_refresh_token("user-abc")
    payload = verify_refresh_token(token)
    assert payload.sub == "user-abc"
    assert payload.type == "refresh"


def test_access_token_rejected_as_refresh() -> None:
    token = create_access_token("u")
    with pytest.raises(TokenVerificationError, match="type"):
        verify_refresh_token(token)


def test_refresh_token_rejected_as_access() -> None:
    token = create_refresh_token("u")
    with pytest.raises(TokenVerificationError, match="type"):
        verify_access_token(token)


def test_tampered_token_fails() -> None:
    token = create_access_token("u")
    tampered = token[:-4] + "AAAA"
    with pytest.raises(TokenVerificationError):
        verify_access_token(tampered)


def test_malformed_token_fails() -> None:
    with pytest.raises(TokenVerificationError):
        verify_access_token("not.a.jwt")


def test_expired_token_raises_specific_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings
    from src.security import jwt as jwt_module

    # Create a token at time T, then verify it at time T + TTL + 1 (after expiry).
    # Start at a fixed past time and token at T.
    fixed_past_time = 1000000
    monkeypatch.setattr(jwt_module, "_now", lambda: fixed_past_time)
    token = create_access_token("u")
    # Now set _now to return a time past the token's exp (fixed_past_time + ttl).
    settings = get_settings()
    future_time = fixed_past_time + settings.jwt_access_ttl_seconds + 100
    monkeypatch.setattr(jwt_module, "_now", lambda: future_time)
    with pytest.raises(TokenExpiredError):
        verify_access_token(token)
