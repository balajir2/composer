"""Tests for LLM provider dispatch."""

import pytest

from src.llm.providers import (
    MissingApiKeyError,
    UnsupportedProviderError,
    build_chat_model,
)


def test_anthropic_returns_chat_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    from langchain_anthropic import ChatAnthropic  # pyright: ignore[reportMissingImports]

    model = build_chat_model("anthropic/claude-3-5-haiku-latest")
    assert isinstance(model, ChatAnthropic)


def test_openai_returns_chat_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    from langchain_openai import ChatOpenAI  # pyright: ignore[reportMissingImports]

    model = build_chat_model("openai/gpt-5-nano")
    assert isinstance(model, ChatOpenAI)


def test_google_returns_chat_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    from langchain_google_genai import ChatGoogleGenerativeAI  # pyright: ignore[reportMissingImports]

    model = build_chat_model("google/gemini-2.0-flash")
    assert isinstance(model, ChatGoogleGenerativeAI)


def test_groq_returns_chat_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    from langchain_groq import ChatGroq  # pyright: ignore[reportMissingImports]

    model = build_chat_model("groq/llama-3.3-70b-versatile")
    assert isinstance(model, ChatGroq)


def test_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(UnsupportedProviderError, match="cohere"):
        build_chat_model("cohere/command-r")


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from src.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY"):
        build_chat_model("anthropic/claude-3-5-haiku-latest")


def test_no_slash_defaults_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matches OAB agent.ts:184-196: no '/' in model string defaults to openai."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    from langchain_openai import ChatOpenAI  # pyright: ignore[reportMissingImports]

    model = build_chat_model("gpt-5-nano")
    assert isinstance(model, ChatOpenAI)


def test_token_limit_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    model = build_chat_model("anthropic/claude-3-5-haiku-latest", token_limit=500)
    assert model.max_tokens == 500  # type: ignore[attr-defined]
