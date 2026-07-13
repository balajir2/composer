"""Tests for AES-256-GCM encrypt/decrypt helpers."""

import base64
import os

import pytest

from src.security.encryption import (
    REDACTED_MARKER,
    SECRET_ENC_PREFIX,
    EncryptionError,
    EncryptionKeyMissingError,
    decrypt,
    decrypt_marked,
    decrypt_sensitive_headers,
    encrypt,
    encrypt_marked,
    encrypt_sensitive_headers,
    is_marked_encrypted,
    is_sensitive_header_name,
    redact_sensitive_headers,
)


def _set_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a valid 32-byte base64 key in settings."""
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    from src.config import get_settings

    get_settings.cache_clear()


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    plaintext = "secret api key value"
    ciphertext = encrypt(plaintext)
    assert isinstance(ciphertext, str)
    assert ciphertext != plaintext
    assert decrypt(ciphertext) == plaintext


def test_encrypt_produces_different_ciphertexts_for_same_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nonce randomness: encrypting the same plaintext twice must differ."""
    _set_key(monkeypatch)
    c1 = encrypt("hello")
    c2 = encrypt("hello")
    assert c1 != c2
    assert decrypt(c1) == decrypt(c2) == "hello"


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(EncryptionKeyMissingError):
        encrypt("anything")


def test_tampered_ciphertext_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    ciphertext = encrypt("important")
    # Flip one byte in the middle
    raw = base64.b64decode(ciphertext.encode())
    mutated = bytes([raw[0] ^ 0x01]) + raw[1:]
    tampered = base64.b64encode(mutated).decode()
    with pytest.raises(EncryptionError):
        decrypt(tampered)


def test_decrypt_rejects_non_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    with pytest.raises(EncryptionError):
        decrypt("not-valid-base64!!")


def test_long_plaintext_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    plaintext = "x" * 10_000
    assert decrypt(encrypt(plaintext)) == plaintext


def test_encrypt_marked_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    marked = encrypt_marked("pinecone-key-123")
    assert marked.startswith(SECRET_ENC_PREFIX)
    assert is_marked_encrypted(marked)
    assert decrypt_marked(marked) == "pinecone-key-123"


def test_decrypt_marked_passes_through_unmarked_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Values saved before encryption was added (plaintext, no prefix) must
    keep working with no backfill migration required — same guarantee
    Jira's decrypt_jira_api_token already gives."""
    _set_key(monkeypatch)
    assert decrypt_marked("plain-legacy-value") == "plain-legacy-value"
    assert is_marked_encrypted("plain-legacy-value") is False


def test_encrypt_marked_wire_compatible_with_jira_prefix() -> None:
    """Same prefix convention as JIRA_TOKEN_ENC_PREFIX — a deliberate
    choice, not a coincidence, so the two schemes can't drift apart."""
    from src.engine.workflow import JIRA_TOKEN_ENC_PREFIX

    assert SECRET_ENC_PREFIX == JIRA_TOKEN_ENC_PREFIX


def test_is_sensitive_header_name_matches_common_auth_headers() -> None:
    assert is_sensitive_header_name("Authorization") is True
    assert is_sensitive_header_name("authorization") is True
    assert is_sensitive_header_name("X-Api-Key") is True
    assert is_sensitive_header_name("Cookie") is True
    assert is_sensitive_header_name("Content-Type") is False
    assert is_sensitive_header_name("Accept") is False


def test_redact_sensitive_headers_masks_only_sensitive_values() -> None:
    headers = {
        "Authorization": "Bearer secret-token",
        "Content-Type": "application/json",
        "X-Api-Key": "key-123",
    }
    redacted = redact_sensitive_headers(headers)
    assert redacted["Authorization"] == REDACTED_MARKER
    assert redacted["X-Api-Key"] == REDACTED_MARKER
    assert redacted["Content-Type"] == "application/json"


def test_encrypt_decrypt_sensitive_headers_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    headers = {"Authorization": "Bearer secret-token", "Content-Type": "application/json"}
    encrypted = encrypt_sensitive_headers(headers)
    assert encrypted["Authorization"] != "Bearer secret-token"
    assert is_marked_encrypted(encrypted["Authorization"])
    assert encrypted["Content-Type"] == "application/json"

    decrypted = decrypt_sensitive_headers(encrypted)
    assert decrypted["Authorization"] == "Bearer secret-token"
    assert decrypted["Content-Type"] == "application/json"


def test_encrypt_sensitive_headers_preserves_prior_on_redacted_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_key(monkeypatch)
    prior_encrypted = encrypt_marked("original-secret")
    incoming = {"Authorization": REDACTED_MARKER}
    result = encrypt_sensitive_headers(incoming, prior_headers={"Authorization": prior_encrypted})
    assert result["Authorization"] == prior_encrypted
