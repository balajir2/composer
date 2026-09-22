"""Tests for the boot-time Postgres → Settings sync of provider API keys."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.security.encryption import encrypt


@dataclass
class _Row:
    """Stand-in for a `LlmApiKey` Prisma row.

    The real model also has updatedAt/createdAt/keyPrefix; the sync only
    touches `provider` + `encryptedKey` + `keyPrefix` (the latter only for
    the decrypt-failure warning), so that's all we model here.
    """

    provider: str
    encryptedKey: str
    keyPrefix: str


def _set_valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a real 32-byte base64 ENCRYPTION_KEY in settings so encrypt() works."""
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    from src.config import get_settings

    get_settings.cache_clear()


def _clear_provider_fields(*fields: str) -> None:
    """Force-clear settings fields on the cached instance.

    monkeypatch.delenv() only removes from os.environ; pydantic_settings
    still reads from .env which on a dev machine has real provider keys.
    These tests need the fields empty so the sync's "fill in the blanks"
    branch fires.
    """
    from src.config import get_settings

    settings = get_settings()
    for f in fields:
        setattr(settings, f, "")


def _make_db(rows: list[_Row], *, query_raises: BaseException | None = None) -> Any:
    """Build a minimal mock Prisma client that returns `rows` from llmapikey.find_many()."""
    db = MagicMock()
    if query_raises is None:
        db.llmapikey.find_many = AsyncMock(return_value=rows)
    else:
        db.llmapikey.find_many = AsyncMock(side_effect=query_raises)
    return db


@pytest.mark.asyncio
async def test_sync_populates_empty_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DB row with a recognised provider fills the matching settings field."""
    _set_valid_encryption_key(monkeypatch)
    from src.config import get_settings
    from src.security.key_sync import sync_llm_keys_from_db

    get_settings.cache_clear()
    _clear_provider_fields("anthropic_api_key", "openai_api_key", "resend_api_key")

    db = _make_db(
        [
            _Row("anthropic", encrypt("sk-ant-real"), "sk-ant"),
            _Row("openai", encrypt("sk-openai-real"), "sk-ope"),
            _Row("resend", encrypt("re-live-real"), "re-liv"),
        ]
    )
    populated = await sync_llm_keys_from_db(db)

    assert populated == {
        "anthropic": "anthropic_api_key",
        "openai": "openai_api_key",
        "resend": "resend_api_key",
    }
    settings = get_settings()
    assert settings.anthropic_api_key == "sk-ant-real"
    assert settings.openai_api_key == "sk-openai-real"
    assert settings.resend_api_key == "re-live-real"


@pytest.mark.asyncio
async def test_sync_preserves_env_set_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a settings field already has a value (env-set), the DB row must NOT override.

    This preserves the documented `.env.example` semantic: "DEV-ONLY
    override" — local devs can shadow prod keys via .env.
    """
    _set_valid_encryption_key(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    from src.config import get_settings
    from src.security.key_sync import sync_llm_keys_from_db

    get_settings.cache_clear()
    db = _make_db([_Row("anthropic", encrypt("sk-ant-from-db"), "sk-ant")])
    populated = await sync_llm_keys_from_db(db)

    assert populated == {}, "env-set fields must not be overridden"
    assert get_settings().anthropic_api_key == "sk-ant-from-env"


@pytest.mark.asyncio
async def test_sync_skips_unknown_provider(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_valid_encryption_key(monkeypatch)
    from src.security.key_sync import sync_llm_keys_from_db

    db = _make_db([_Row("aurora-borealis", encrypt("whatever"), "whatev")])
    with caplog.at_level("WARNING", logger="src.security.key_sync"):
        populated = await sync_llm_keys_from_db(db)

    assert populated == {}
    assert any("aurora-borealis" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_sync_handles_decrypt_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad-ciphertext row must be logged and skipped — startup keeps going.

    This is the exact failure mode for an ENCRYPTION_KEY rotation without
    re-encryption: every row will fail to decrypt, but the app must boot.
    """
    _set_valid_encryption_key(monkeypatch)
    from src.config import get_settings
    from src.security.key_sync import sync_llm_keys_from_db

    get_settings.cache_clear()
    _clear_provider_fields("anthropic_api_key", "openai_api_key")

    # Garbage ciphertext: valid base64 but won't decrypt.
    bad_ct = base64.b64encode(b"x" * 64).decode()
    db = _make_db(
        [
            _Row("anthropic", bad_ct, "sk-ant"),
            _Row("openai", encrypt("sk-openai-ok"), "sk-ope"),
        ]
    )

    with caplog.at_level("WARNING", logger="src.security.key_sync"):
        populated = await sync_llm_keys_from_db(db)

    # Bad row skipped, good row applied — partial sync is the contract.
    assert populated == {"openai": "openai_api_key"}
    assert get_settings().openai_api_key == "sk-openai-ok"
    assert get_settings().anthropic_api_key == ""
    assert any("failed to decrypt anthropic" in r.message for r in caplog.records), (
        "expected a warning naming the failing provider"
    )


@pytest.mark.asyncio
async def test_sync_survives_db_query_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If the DB itself raises (connection down, table missing), don't crash."""
    _set_valid_encryption_key(monkeypatch)
    from src.security.key_sync import sync_llm_keys_from_db

    db = _make_db([], query_raises=RuntimeError("connection refused"))

    with caplog.at_level("WARNING", logger="src.security.key_sync"):
        populated = await sync_llm_keys_from_db(db)

    assert populated == {}
    assert any("connection refused" in r.message for r in caplog.records)


def test_typesafe_in_provider_to_settings_field() -> None:
    from src.security.key_sync import PROVIDER_TO_SETTINGS_FIELD

    assert PROVIDER_TO_SETTINGS_FIELD["typesafe"] == "typesafe_api_key"


@pytest.mark.asyncio
async def test_sync_empty_table_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_valid_encryption_key(monkeypatch)
    from src.config import get_settings
    from src.security.key_sync import sync_llm_keys_from_db

    get_settings.cache_clear()
    _clear_provider_fields("anthropic_api_key")

    db = _make_db([])
    populated = await sync_llm_keys_from_db(db)

    assert populated == {}
    assert get_settings().anthropic_api_key == ""
