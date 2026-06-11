"""Unit tests for provider key test helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.api.admin_llm_keys_test as key_tests


@pytest.mark.asyncio
async def test_resend_accepts_sending_only_restricted_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        key_tests,
        "_get",
        AsyncMock(
            return_value=SimpleNamespace(
                status_code=401,
                text='{"statusCode":401,"message":"This API key is restricted to only send emails","name":"restricted_api_key"}',
            )
        ),
    )

    result = await key_tests.run_key_test("resend", "re_live_sending_only")

    assert result.ok is True
    assert result.status == 401
    assert "sending-only key accepted" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "url", "message"),
    [
        ("deepseek", "https://api.deepseek.com/models", "DeepSeek key valid"),
        (
            "qwen",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
            "Qwen key valid",
        ),
    ],
)
async def test_chinese_llm_provider_key_tests_use_models_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    url: str,
    message: str,
) -> None:
    get_mock = AsyncMock(return_value=SimpleNamespace(status_code=200, text="{}"))
    monkeypatch.setattr(key_tests, "_get", get_mock)

    result = await key_tests.run_key_test(provider, "provider-key")

    assert result.ok is True
    assert result.status == 200
    assert message in result.message
    get_mock.assert_awaited_once_with(
        url,
        headers={"Authorization": "Bearer provider-key"},
    )
