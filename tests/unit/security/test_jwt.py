"""Tests for JWT primitives."""

import time

import pytest

from src.security.jwt import (
    TokenExpiredError,
    TokenVerificationError,
    create_access_token,
    create_password_change_token,
    create_refresh_token,
    verify_access_token,
    verify_password_change_token,
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


def test_create_and_verify_password_change_token() -> None:
    token = create_password_change_token("u1")
    payload = verify_password_change_token(token)
    assert payload.sub == "u1"
    assert payload.type == "password_change"


def test_password_change_token_rejects_access_token() -> None:
    access = create_access_token("u1")
    with pytest.raises(TokenVerificationError):
        verify_password_change_token(access)


def test_password_change_token_respects_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.setenv("JWT_PASSWORD_CHANGE_TTL_SECONDS", "1")
    get_settings.cache_clear()
    token = create_password_change_token("u1")
    # `_now()`/jose's exp check both truncate to whole seconds and jose requires
    # a strict `exp < now` (see jose.jwt._validate_exp), so with a 1s TTL the
    # elapsed wall-clock time must push the truncated "now" at least two whole
    # seconds past the truncated `iat` to deterministically clear the boundary
    # regardless of where `iat`'s fractional second falls. 1.2s left this
    # flaky (~50% failure rate); 2.2s gives a safe margin above the 2.0s floor.
    time.sleep(2.2)
    with pytest.raises(TokenVerificationError):
        verify_password_change_token(token)
    get_settings.cache_clear()


def test_create_and_verify_approval_email_token() -> None:
    from src.security.jwt import create_approval_email_token, verify_approval_email_token

    token = create_approval_email_token(
        "exec-1", "approval-1", "approved", "reviewer@example.com", "2026-07-11T10:00:00+00:00"
    )
    payload = verify_approval_email_token(token)
    assert payload.sub == "exec-1"
    assert payload.node_id == "approval-1"
    assert payload.decision == "approved"
    assert payload.approver_email == "reviewer@example.com"
    assert payload.pending_since == "2026-07-11T10:00:00+00:00"
    assert payload.type == "approval_email"


def test_approval_email_token_rejects_access_token() -> None:
    from src.security.jwt import create_access_token, verify_approval_email_token

    access = create_access_token("u1")
    with pytest.raises(TokenVerificationError):
        verify_approval_email_token(access)


def test_approval_email_token_respects_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings
    from src.security.jwt import create_approval_email_token, verify_approval_email_token

    monkeypatch.setenv("APPROVAL_LINK_TTL_HOURS", "0")
    get_settings.cache_clear()
    token = create_approval_email_token(
        "exec-1", "approval-1", "rejected", "reviewer@example.com", "2026-07-11T10:00:00+00:00"
    )
    time.sleep(2.2)
    with pytest.raises(TokenVerificationError):
        verify_approval_email_token(token)
    get_settings.cache_clear()
