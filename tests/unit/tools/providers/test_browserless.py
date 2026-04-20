"""Tests for BrowserlessProvider (headless-browser page fetch)."""

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.browserless import BrowserlessProvider, MissingApiKeyError


def _agent_node() -> AgentNode:
    return AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "A"},
        }
    )


def test_provider_metadata() -> None:
    p = BrowserlessProvider()
    assert p.name == "browserless"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "BROWSERLESS_API_KEY"


async def test_tools_lists_fetch() -> None:
    tools = await BrowserlessProvider().tools()
    assert [t.name for t in tools] == ["browserless_fetch"]


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERLESS_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(MissingApiKeyError):
        await BrowserlessProvider().build_tool(
            "browserless_fetch",
            BuildContext(node=_agent_node(), state=initial_state()),
        )


async def test_fetch_tool_returns_html(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("BROWSERLESS_API_KEY", "bl-test")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://chrome.browserless.io/content?token=bl-test",
        method="POST",
        text="<html><body><h1>Hello</h1></body></html>",
    )
    tool = await BrowserlessProvider().build_tool(
        "browserless_fetch",
        BuildContext(node=_agent_node(), state=initial_state()),
    )
    result = await tool._arun(url="https://example.com")  # pyright: ignore[reportPrivateUsage]
    assert "<h1>Hello</h1>" in result


async def test_fetch_tool_http_error(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("BROWSERLESS_API_KEY", "bl-test")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://chrome.browserless.io/content?token=bl-test",
        method="POST",
        status_code=500,
        text="err",
    )
    tool = await BrowserlessProvider().build_tool(
        "browserless_fetch",
        BuildContext(node=_agent_node(), state=initial_state()),
    )
    assert (await tool._arun(url="https://example.com")).startswith(  # pyright: ignore[reportPrivateUsage]
        "Error: Browserless fetch failed (HTTP 500)"
    )
