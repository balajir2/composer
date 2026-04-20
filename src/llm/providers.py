"""LLM provider dispatch.

Maps `"provider/modelname"` strings to LangChain chat-model instances.
See ADR-0006; spec §5.
"""

from typing import Any

from langchain_anthropic import ChatAnthropic  # pyright: ignore[reportMissingImports]
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI  # pyright: ignore[reportMissingImports]
from langchain_groq import ChatGroq  # pyright: ignore[reportMissingImports]
from langchain_openai import ChatOpenAI  # pyright: ignore[reportMissingImports]

from src.config import get_settings


class UnsupportedProviderError(ValueError):
    """Raised when the provider prefix in the model string isn't one of
    anthropic / openai / google / groq."""


class MissingApiKeyError(RuntimeError):
    """Raised when the configured provider's API key is empty."""


_KEY_MAP: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
    "groq": "groq_api_key",
}


def build_chat_model(
    model_string: str,
    *,
    token_limit: int | None = None,
    temperature: float | None = None,
    extra: dict[str, Any] | None = None,
) -> BaseChatModel:
    """Parse `model_string` as `provider/modelname`, return a LangChain chat model.

    Providers: 'anthropic', 'openai', 'google', 'groq'.
    No prefix → defaults to 'openai' (matches OAB agent.ts:184-196).

    Raises:
        UnsupportedProviderError: unknown provider.
        MissingApiKeyError: provider's key is empty in Settings.
    """
    if "/" in model_string:
        provider, _, model_name = model_string.partition("/")
    else:
        provider, model_name = "openai", model_string

    if provider not in _KEY_MAP:
        raise UnsupportedProviderError(
            f"Unknown provider {provider!r}. Supported: {sorted(_KEY_MAP)}"
        )

    settings = get_settings()
    key_field = _KEY_MAP[provider]
    api_key = getattr(settings, key_field, "")
    if not api_key:
        env_var = key_field.upper()
        raise MissingApiKeyError(f"{env_var} is not set. Add it to .env or deployment env vars.")

    extra_kwargs = extra or {}

    if provider == "anthropic":
        kwargs: dict[str, Any] = {"model": model_name, "api_key": api_key, **extra_kwargs}
        if token_limit is not None:
            kwargs["max_tokens"] = token_limit
        if temperature is not None:
            kwargs["temperature"] = temperature
        return ChatAnthropic(**kwargs)  # pyright: ignore[reportCallIssue]

    if provider == "openai":
        kwargs = {"model": model_name, "api_key": api_key, **extra_kwargs}
        if token_limit is not None:
            kwargs["max_tokens"] = token_limit
        if temperature is not None:
            kwargs["temperature"] = temperature
        return ChatOpenAI(**kwargs)  # pyright: ignore[reportCallIssue]

    if provider == "google":
        kwargs = {"model": model_name, "google_api_key": api_key, **extra_kwargs}
        if token_limit is not None:
            kwargs["max_output_tokens"] = token_limit
        if temperature is not None:
            kwargs["temperature"] = temperature
        return ChatGoogleGenerativeAI(**kwargs)  # pyright: ignore[reportCallIssue]

    # groq
    kwargs = {"model": model_name, "api_key": api_key, **extra_kwargs}
    if token_limit is not None:
        kwargs["max_tokens"] = token_limit
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatGroq(**kwargs)  # pyright: ignore[reportCallIssue]


__all__ = [
    "MissingApiKeyError",
    "UnsupportedProviderError",
    "build_chat_model",
]
