"""Tests for the ToolProvider ABC + AuthRequirement hierarchy."""

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from src.tools.base import (
    ApiKeyAuth,
    BuildContext,
    HealthStatus,
    NoAuth,
    OAuthAuth,
    ToolDefinition,
    ToolProvider,
)


def test_api_key_auth_dataclass() -> None:
    auth = ApiKeyAuth(env_var="FOO_KEY", settings_field="foo_api_key")
    assert auth.env_var == "FOO_KEY"
    assert auth.settings_field == "foo_api_key"
    assert auth.required is True


def test_no_auth_dataclass() -> None:
    auth = NoAuth()
    assert auth.required is False


def test_oauth_auth_has_rfc8707_default() -> None:
    auth = OAuthAuth(
        authorize_url="https://example.com/a",
        token_url="https://example.com/t",
        scopes=["read"],
    )
    assert auth.include_rfc8707_resource is True


def test_tool_definition_is_frozen() -> None:
    class _InputSchema(BaseModel):
        x: int

    td = ToolDefinition(name="t1", description="d", args_schema=_InputSchema)
    with pytest.raises((AttributeError, TypeError)):
        td.name = "t2"  # type: ignore[misc]


class _FakeProvider(ToolProvider):
    name = "fake"
    description = "fake provider for contract tests"
    category = "standard"
    auth = NoAuth()

    async def tools(self) -> list[ToolDefinition]:
        class _In(BaseModel):
            x: int

        return [ToolDefinition(name="fake_tool", description="d", args_schema=_In)]

    async def build_tool(self, tool_name: str, context: BuildContext) -> BaseTool:
        raise NotImplementedError("contract test only")


async def test_provider_default_health_check_no_auth_is_ok() -> None:
    provider = _FakeProvider()
    status = await provider.health_check()
    assert status.ok is True
    assert "no auth" in status.message.lower()


async def test_provider_default_health_check_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
    from src.config import get_settings

    get_settings.cache_clear()

    class _KeyedProvider(_FakeProvider):
        auth = ApiKeyAuth(env_var="TAVILY_API_KEY", settings_field="tavily_api_key")

    status = await _KeyedProvider().health_check()
    assert status.ok is False
    assert "TAVILY_API_KEY" in status.message
    assert "missing" in status.message.lower()


def test_health_status_is_frozen() -> None:
    s = HealthStatus(ok=True, message="up")
    with pytest.raises((AttributeError, TypeError)):
        s.ok = False  # type: ignore[misc]
