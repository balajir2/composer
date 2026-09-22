"""Unit tests for provider key test helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
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


@pytest.mark.asyncio
async def test_typesafe_key_test_ok_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, object]
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"answers": {}}, request=request)

    monkeypatch.setattr(key_tests, "_post", _fake_post)
    result = await key_tests.run_key_test("typesafe", "sk-test")
    assert result.ok is True


@pytest.mark.asyncio
async def test_typesafe_key_test_fails_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, object]
    ) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(401, text="bad key", request=request)

    monkeypatch.setattr(key_tests, "_post", _fake_post)
    result = await key_tests.run_key_test("typesafe", "sk-bad")
    assert result.ok is False
    assert result.status == 401
