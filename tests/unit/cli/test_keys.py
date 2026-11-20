"""Unit tests for composer keys CLI — pure logic only.

Mapping tests are pure; the command tests fake the one-shot session and
patch the repository functions, so no Postgres is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from src.cli import keys as cli_keys
from src.cli.keys import _PROVIDER_TO_ENV  # pyright: ignore[reportPrivateUsage]
from src.db.repos import llm_api_keys as keys_repo
from src.db.repos.llm_api_keys import LlmApiKeyRecord, UpsertResult
from tests.unit.db_fakes import FakeSession, fake_scope

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

_EXPECTED_PROVIDERS = {
    "anthropic",
    "openai",
    "google",
    "groq",
    "deepseek",
    "qwen",
    "langsmith",
    "tavily",
    "firecrawl",
    "serper",
    "browserless",
    "gamma",
    "resend",
    "typesafe",
}

_EXPECTED_ENV_VARS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "QWEN_API_KEY",
    "LANGCHAIN_API_KEY",
    "TAVILY_API_KEY",
    "FIRECRAWL_API_KEY",
    "SERPER_API_KEY",
    "BROWSERLESS_API_KEY",
    "GAMMA_API_KEY",
    "RESEND_API_KEY",
    "TYPESAFE_API_KEY",
}


def test_provider_to_env_mapping_complete() -> None:
    """All expected providers must be present in _PROVIDER_TO_ENV."""
    assert set(_PROVIDER_TO_ENV.keys()) == _EXPECTED_PROVIDERS


def test_provider_to_env_openai_maps_correctly() -> None:
    assert _PROVIDER_TO_ENV["openai"] == "OPENAI_API_KEY"


def test_provider_to_env_langsmith_maps_to_langchain_key() -> None:
    """langsmith provider maps to LANGCHAIN_API_KEY (matching LangChain convention)."""
    assert _PROVIDER_TO_ENV["langsmith"] == "LANGCHAIN_API_KEY"


def test_provider_to_env_values_are_unique() -> None:
    """No two providers should map to the same env var."""
    values = list(_PROVIDER_TO_ENV.values())
    assert len(values) == len(set(values))


def test_provider_to_env_all_env_vars_expected() -> None:
    """The set of env-var values must exactly match the expected set."""
    assert set(_PROVIDER_TO_ENV.values()) == _EXPECTED_ENV_VARS


def test_typesafe_in_provider_to_env() -> None:
    assert _PROVIDER_TO_ENV["typesafe"] == "TYPESAFE_API_KEY"


_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _record(provider: str = "openai") -> LlmApiKeyRecord:
    return LlmApiKeyRecord("id1", provider, "ct", "sk-abc", _NOW, _NOW)


def _fake_encrypt(value: str) -> str:
    return f"enc:{value}"


def _fake_decrypt(_ciphertext: str) -> str:
    return "plain"


def _fake_session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    session = FakeSession()

    def _scope(_settings: object) -> AbstractAsyncContextManager[FakeSession]:
        return fake_scope(session)

    monkeypatch.setattr(cli_keys, "one_shot_session", _scope)
    return session


async def test_keys_list_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = _fake_session(monkeypatch)
    monkeypatch.setattr(keys_repo, "list_keys", AsyncMock(return_value=[]))
    assert await cli_keys.keys_list() == 0
    assert capsys.readouterr().out == "(no keys set)\n"
    session.commit.assert_not_called()


async def test_keys_list_prints_prefix_and_updated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_session(monkeypatch)
    monkeypatch.setattr(keys_repo, "list_keys", AsyncMock(return_value=[_record()]))
    assert await cli_keys.keys_list() == 0
    assert f"openai           sk-abc...  (updated {_NOW})" in capsys.readouterr().out


@pytest.mark.parametrize(("created", "verb"), [(True, "created"), (False, "updated")])
async def test_keys_set_reports_created_or_updated_and_commits(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    created: bool,
    verb: str,
) -> None:
    session = _fake_session(monkeypatch)
    monkeypatch.setattr(cli_keys, "encrypt", _fake_encrypt)
    upsert = AsyncMock(return_value=UpsertResult(_record(), created))
    monkeypatch.setattr(keys_repo, "upsert", upsert)
    assert await cli_keys.keys_set("openai", "sk-abcdef") == 0
    assert capsys.readouterr().out == f"{verb} key for openai (prefix sk-abc)\n"
    assert upsert.await_args is not None
    assert upsert.await_args.kwargs == {"encrypted_key": "enc:sk-abcdef", "key_prefix": "sk-abc"}
    session.commit.assert_awaited_once()


async def test_keys_set_rejects_unsupported_provider() -> None:
    assert await cli_keys.keys_set("bogus", "x") == 1


async def test_keys_delete_missing_returns_1_without_commit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = _fake_session(monkeypatch)
    monkeypatch.setattr(keys_repo, "delete", AsyncMock(return_value=False))
    assert await cli_keys.keys_delete("openai") == 1
    assert capsys.readouterr().err == "no key set for openai\n"
    session.commit.assert_not_called()


async def test_keys_delete_present_commits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = _fake_session(monkeypatch)
    monkeypatch.setattr(keys_repo, "delete", AsyncMock(return_value=True))
    assert await cli_keys.keys_delete("openai") == 0
    assert capsys.readouterr().out == "deleted key for openai\n"
    session.commit.assert_awaited_once()


async def test_keys_sync_posts_decrypted_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_session(monkeypatch)
    monkeypatch.setenv("VERCEL_API_TOKEN", "t")
    monkeypatch.setenv("VERCEL_PROJECT_ID", "p")
    monkeypatch.setattr(keys_repo, "list_keys", AsyncMock(return_value=[_record()]))
    monkeypatch.setattr(cli_keys, "decrypt", _fake_decrypt)
    upsert_env = AsyncMock()

    class _Client:
        def __init__(self, **_kw: str) -> None:
            self.upsert_env = upsert_env

    monkeypatch.setattr(cli_keys, "VercelClient", _Client)
    assert await cli_keys.keys_sync("vercel", False) == 0
    assert "posted=['OPENAI_API_KEY'], pruned=[]" in capsys.readouterr().out
    assert upsert_env.await_args is not None
    assert upsert_env.await_args.args[0].value == "plain"
