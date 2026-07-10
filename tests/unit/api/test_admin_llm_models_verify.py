"""Unit tests for LLM model verification helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
