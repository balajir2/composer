"""Unit tests for LLM model verification helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import src.api.admin_llm_models_verify as verify_mod


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model_id", "url"),
    [
        ("deepseek", "deepseek-chat", "https://api.deepseek.com/chat/completions"),
        (
            "qwen",
            "qwen-plus",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
    ],
)
async def test_openai_compatible_chinese_model_verify_routes_to_provider_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model_id: str,
    url: str,
) -> None:
    post_mock = AsyncMock(return_value=SimpleNamespace(status_code=200, text="{}"))
    monkeypatch.setattr(verify_mod, "_post", post_mock)

    result = await verify_mod.run_model_verify(provider, model_id, "provider-key")

    assert result.status == "ok"
    post_mock.assert_awaited_once()
    call_args = post_mock.await_args
    assert call_args is not None
    assert call_args.args[0] == url
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer provider-key"
    assert call_args.kwargs["json"]["model"] == model_id
    assert call_args.kwargs["json"]["messages"][0]["content"] == "hi"


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", ["gpt-5.2", "gpt-5.4-nano", "gpt-6-astra", "o3-mini"])
async def test_verify_openai_always_uses_max_completion_tokens_with_headroom(
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
) -> None:
    """Every current OpenAI chat model (not just the o<N> reasoning family)
    now rejects `max_tokens` with a 400 'Unsupported parameter' error, and a
    1-token budget is too small for a reasoning model's hidden
    reasoning_content, itself producing a 400 whose message contains the
    word "model" -- misread by _classify() as model-unavailable either way.
    The probe must send `max_completion_tokens` (never `max_tokens`) with
    enough headroom to survive reasoning overhead, for every OpenAI model,
    not just ones matching an o<N> name pattern."""
    post_mock = AsyncMock(return_value=SimpleNamespace(status_code=200, text="{}"))
    monkeypatch.setattr(verify_mod, "_post", post_mock)

    result = await verify_mod.run_model_verify("openai", model_id, "provider-key")

    assert result.status == "ok"
    call_args = post_mock.await_args
    assert call_args is not None
    body = call_args.kwargs["json"]
    assert "max_tokens" not in body
    assert body["max_completion_tokens"] >= 16


@pytest.mark.asyncio
async def test_verify_typesafe_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, object]
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"answers": {}}, request=request)

    monkeypatch.setattr(verify_mod, "_post", _fake_post)
    result = await verify_mod.run_model_verify("typesafe", "jev-latest", "sk-test")
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_verify_typesafe_unavailable_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, object]
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(404, text="model not found", request=request)

    monkeypatch.setattr(verify_mod, "_post", _fake_post)
    result = await verify_mod.run_model_verify("typesafe", "jev-nonexistent", "sk-test")
    assert result.status == "unavailable"


@pytest.mark.asyncio
async def test_verify_typesafe_auth_error_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, object]
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(401, text="bad key", request=request)

    monkeypatch.setattr(verify_mod, "_post", _fake_post)
    result = await verify_mod.run_model_verify("typesafe", "jev-latest", "sk-bad")
    assert result.status == "auth_error"
