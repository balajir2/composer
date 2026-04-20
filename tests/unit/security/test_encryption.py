"""Tests for AES-256-GCM encrypt/decrypt helpers."""

import base64
import os

import pytest

from src.security.encryption import (
    EncryptionError,
    EncryptionKeyMissingError,
    decrypt,
    encrypt,
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
