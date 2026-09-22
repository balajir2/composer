"""Unit tests for composer keys CLI — pure logic only.

DB-backed tests (keys_list, keys_set, keys_delete, keys_sync) are deferred
to integration coverage in Phase 9 Task 12's phase-exit verification.
These tests exercise the provider-to-env-var mapping and the stdin-read
branch without touching Postgres.
"""

from __future__ import annotations

from src.cli.keys import _PROVIDER_TO_ENV  # pyright: ignore[reportPrivateUsage]

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
